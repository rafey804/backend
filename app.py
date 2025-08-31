from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
import re

app = Flask(__name__)
CORS(app, origins=[
    "http://localhost:3001", 
    "https://snapsavepro.com", 
    "http://snapsavepro.com"
], supports_credentials=True)

# Store download progress and files
download_progress = {}
download_files = {}  # Store file paths
executor = ThreadPoolExecutor(max_workers=3)  # Concurrent downloads

class ProgressHook:
    def __init__(self, download_id):
        self.download_id = download_id
    
    def __call__(self, d):
        if d['status'] == 'downloading':
            try:
                percent = d.get('_percent_str', '0%').replace('%', '')
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                
                download_progress[self.download_id] = {
                    'status': 'downloading',
                    'percent': float(percent) if percent != 'N/A' else 0,
                    'speed': speed,
                    'eta': eta,
                    'downloaded_bytes': d.get('downloaded_bytes', 0),
                    'total_bytes': d.get('total_bytes', 0)
                }
            except:
                pass
        elif d['status'] == 'finished':
            download_progress[self.download_id] = {
                'status': 'processing',
                'percent': 95,
                'filename': d['filename']
            }

def detect_platform(url):
    """Detect if URL is TikTok or Instagram"""
    url = url.strip().lower()
    
    # TikTok patterns
    tiktok_patterns = [
        r'(?:https?://)?(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+',
        r'(?:https?://)?(?:vm|vt)\.tiktok\.com/[\w.-]+',
        r'(?:https?://)?(?:www\.)?tiktok\.com/t/[\w.-]+',
        r'(?:https?://)?m\.tiktok\.com/v/\d+',
    ]
    
    for pattern in tiktok_patterns:
        if re.match(pattern, url):
            return 'tiktok'
    
    # Instagram patterns
    instagram_patterns = [
        r'(?:https?://)?(?:www\.)?instagram\.com/p/[\w-]+/?',  # Posts
        r'(?:https?://)?(?:www\.)?instagram\.com/reel/[\w-]+/?',  # Reels  
        r'(?:https?://)?(?:www\.)?instagram\.com/reels/[\w-]+/?',  # Reels alternative
        r'(?:https?://)?(?:www\.)?instagram\.com/stories/[\w.-]+/\d+/?',  # Stories
        r'(?:https?://)?(?:www\.)?instagram\.com/tv/[\w-]+/?',  # IGTV
        r'(?:https?://)?(?:www\.)?instagram\.com/[\w.-]+/p/[\w-]+/?',  # Alternative post format
    ]
    
    for pattern in instagram_patterns:
        if re.match(pattern, url):
            return 'instagram'
    
    return 'unknown'

def get_tiktok_ydl_opts(base_opts=None):
    """TikTok-specific yt-dlp options"""
    if base_opts is None:
        base_opts = {}
    
    tiktok_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 60,
        'retries': 5,
        'fragment_retries': 5,
        'file_access_retries': 3,
        'extractor_retries': 3,
        'skip_unavailable_fragments': True,
        'ignoreerrors': False,
        'no_color': True,
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1',
        'format_sort': ['res', 'ext:mp4:m4a'],
        'format_sort_force': True,
        'http_chunk_size': 5242880,
        'concurrent_fragment_downloads': 2,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Referer': 'https://www.tiktok.com/',
            'Origin': 'https://www.tiktok.com',
        }
    }
    
    tiktok_opts.update(base_opts)
    return tiktok_opts

def get_instagram_ydl_opts(base_opts=None):
    """Instagram-specific yt-dlp options"""
    if base_opts is None:
        base_opts = {}
    
    instagram_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 90,
        'retries': 5,
        'fragment_retries': 5,
        'file_access_retries': 3,
        'extractor_retries': 3,
        'skip_unavailable_fragments': True,
        'ignoreerrors': False,
        'no_color': True,
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1',
        'format_sort': ['res', 'ext:mp4:m4a'],
        'format_sort_force': True,
        'http_chunk_size': 8388608,  # 8MB chunks
        'concurrent_fragment_downloads': 2,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'en-us,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.instagram.com/',
            'Origin': 'https://www.instagram.com',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
        }
    }
    
    instagram_opts.update(base_opts)
    return instagram_opts

def get_instagram_content_type(url):
    """Determine Instagram content type"""
    if '/p/' in url:
        return 'post'
    elif '/reel/' in url or '/reels/' in url:
        return 'reel'
    elif '/stories/' in url:
        return 'story'
    elif '/tv/' in url:
        return 'igtv'
    else:
        return 'post'  # Default to post

@app.route('/api/video-info', methods=['POST'])
def get_video_info():
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        platform = detect_platform(url)
        
        if platform == 'unknown':
            return jsonify({'error': 'Please provide a valid TikTok or Instagram URL'}), 400

        print(f"Processing {platform.upper()} URL: {url}")

        # TIKTOK CODE
        if platform == 'tiktok':
            ydl_opts = get_tiktok_ydl_opts({
                'noplaylist': True,
                'extract_flat': False,
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return jsonify({'error': 'Could not extract TikTok video information'}), 400
                
                duration = info.get('duration', 0)
                print(f"TikTok video duration: {duration} seconds")
                
                # Better thumbnail handling for TikTok
                thumbnail_url = ''
                if info.get('thumbnail'):
                    thumbnail_url = info.get('thumbnail')
                elif info.get('thumbnails'):
                    thumbnails = info.get('thumbnails', [])
                    if thumbnails:
                        best_thumb = max(thumbnails, key=lambda t: (t.get('width', 0) * t.get('height', 0)))
                        thumbnail_url = best_thumb.get('url', '')
                
                video_info = {
                    'title': info.get('title', info.get('fulltitle', 'TikTok Video')),
                    'duration': duration,
                    'view_count': info.get('view_count', 0),
                    'uploader': info.get('uploader', info.get('creator', info.get('uploader_id', 'Unknown'))),
                    'uploader_id': info.get('uploader_id', ''),
                    'thumbnail': thumbnail_url,
                    'description': (info.get('description', '')[:200] + '...') if info.get('description', '') else '',
                    'upload_date': info.get('upload_date', ''),
                    'like_count': info.get('like_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'repost_count': info.get('repost_count', 0),
                    'platform': 'tiktok',
                    'formats': []
                }
                
                formats = info.get('formats', [])
                if not formats:
                    return jsonify({'error': 'No formats available for this TikTok video'}), 400
                
                video_formats = []
                audio_formats = []
                
                for fmt in formats:
                    if not fmt.get('format_id'):
                        continue
                        
                    vcodec = fmt.get('vcodec', 'none')
                    acodec = fmt.get('acodec', 'none')
                    height = fmt.get('height') or 0
                    ext = fmt.get('ext', '')
                    
                    if ext not in ['mp4', 'm4a', 'webm', 'mkv']:
                        continue
                    
                    if vcodec != 'none' and height > 0:
                        video_formats.append(fmt)
                    elif acodec != 'none' and vcodec == 'none':
                        audio_formats.append(fmt)
                
                processed_video = []
                processed_audio = []
                
                # Process video formats
                for fmt in sorted(video_formats, key=lambda x: x.get('height') or 0, reverse=True):
                    height = fmt.get('height') or 0
                    if height < 144:
                        continue
                        
                    quality = f"{height}p"
                    ext = fmt.get('ext', 'mp4')
                    
                    format_data = {
                        'quality': quality,
                        'type': 'video',
                        'format_id': fmt['format_id'],
                        'ext': ext,
                        'filesize': fmt.get('filesize', 0),
                        'fps': fmt.get('fps', 30),
                        'width': fmt.get('width', 0),
                        'height': height,
                        'has_audio': fmt.get('acodec') and fmt.get('acodec') != 'none',
                        'platform': 'tiktok',
                        'watermark_free': True,
                        'duration': duration
                    }
                    processed_video.append(format_data)
                
                # Process audio formats
                for fmt in sorted(audio_formats, key=lambda x: x.get('abr') or 0, reverse=True):
                    abr = fmt.get('abr') or 128
                    quality_level = f"{int(abr)}kbps" if abr else "128kbps"
                    
                    format_data = {
                        'quality': quality_level,
                        'type': 'audio',
                        'format_id': fmt['format_id'],
                        'ext': fmt.get('ext', 'm4a'),
                        'filesize': fmt.get('filesize', 0),
                        'abr': abr,
                        'platform': 'tiktok',
                        'description': f"Audio ({quality_level})"
                    }
                    processed_audio.append(format_data)
                
                video_info['formats'] = {
                    'video_formats': processed_video[:8],
                    'audio_formats': processed_audio[:6]
                }
                
                print(f"TikTok formats found - Video: {len(processed_video)}, Audio: {len(processed_audio)}")
                return jsonify(video_info)

        # INSTAGRAM CODE
        else:  # instagram
            ydl_opts = get_instagram_ydl_opts({
                'noplaylist': True,
                'extract_flat': False,
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return jsonify({'error': 'Could not extract Instagram content information'}), 400
                
                duration = info.get('duration', 0)
                content_type = get_instagram_content_type(url)
                
                # Better thumbnail handling for Instagram
                thumbnail_url = ''
                if info.get('thumbnail'):
                    thumbnail_url = info.get('thumbnail')
                elif info.get('thumbnails'):
                    thumbnails = info.get('thumbnails', [])
                    if thumbnails:
                        best_thumb = max(thumbnails, key=lambda t: (t.get('width', 0) * t.get('height', 0)))
                        thumbnail_url = best_thumb.get('url', '')
                
                # Better title extraction
                title = info.get('title', '')
                if not title or title == 'NA':
                    title = info.get('fulltitle', f'Instagram {content_type.title()}')
                    if not title or title == 'NA':
                        if info.get('uploader'):
                            title = f"{info.get('uploader')} - Instagram {content_type.title()}"
                        else:
                            title = f"Instagram {content_type.title()}"
                
                video_info = {
                    'title': title,
                    'duration': duration,
                    'view_count': info.get('view_count', 0),
                    'uploader': info.get('uploader', info.get('creator', info.get('uploader_id', 'Unknown'))),
                    'uploader_id': info.get('uploader_id', ''),
                    'thumbnail': thumbnail_url,
                    'description': (info.get('description', '')[:150] + '...') if info.get('description', '') else '',
                    'upload_date': info.get('upload_date', ''),
                    'like_count': info.get('like_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'content_type': content_type,
                    'platform': 'instagram',
                    'formats': []
                }
                
                formats = info.get('formats', [])
                if not formats:
                    return jsonify({'error': 'No formats available for this Instagram content'}), 400
                
                video_formats = []
                audio_formats = []
                
                for fmt in formats:
                    if not fmt.get('format_id'):
                        continue
                        
                    vcodec = fmt.get('vcodec', 'none')
                    acodec = fmt.get('acodec', 'none')
                    height = fmt.get('height') or 0
                    ext = fmt.get('ext', '')
                    
                    if ext not in ['mp4', 'm4a', 'webm']:
                        continue
                    
                    if vcodec != 'none' and height > 0:
                        video_formats.append(fmt)
                    elif acodec != 'none' and vcodec == 'none':
                        audio_formats.append(fmt)
                
                processed_video = []
                processed_audio = []
                
                # Process video formats
                for fmt in sorted(video_formats, key=lambda x: x.get('height') or 0, reverse=True):
                    height = fmt.get('height') or 0
                    if height < 144:
                        continue
                        
                    quality = f"{height}p"
                    ext = fmt.get('ext', 'mp4')
                    
                    format_data = {
                        'quality': quality,
                        'type': 'video',
                        'format_id': fmt['format_id'],
                        'ext': ext,
                        'filesize': fmt.get('filesize', 0),
                        'width': fmt.get('width', 0),
                        'height': height,
                        'has_audio': fmt.get('acodec') and fmt.get('acodec') != 'none',
                        'content_type': content_type,
                        'platform': 'instagram',
                        'high_quality': height >= 720,
                        'duration': duration
                    }
                    processed_video.append(format_data)
                
                # Process audio formats
                for fmt in sorted(audio_formats, key=lambda x: x.get('abr') or 0, reverse=True):
                    abr = fmt.get('abr') or 128
                    quality_level = f"{int(abr)}kbps" if abr else "128kbps"
                    
                    format_data = {
                        'quality': quality_level,
                        'type': 'audio',
                        'format_id': fmt['format_id'],
                        'ext': fmt.get('ext', 'm4a'),
                        'filesize': fmt.get('filesize', 0),
                        'abr': abr,
                        'platform': 'instagram',
                        'description': f"Audio ({quality_level})"
                    }
                    processed_audio.append(format_data)
                
                video_info['formats'] = {
                    'video_formats': processed_video[:6],
                    'audio_formats': processed_audio[:6],
                    'content_type': content_type
                }
                
                print(f"Instagram formats found - Video: {len(processed_video)}, Audio: {len(processed_audio)}")
                return jsonify(video_info)
            
    except yt_dlp.DownloadError as e:
        error_msg = str(e)
        platform = detect_platform(url) if 'url' in locals() else 'video'
        
        if 'timeout' in error_msg.lower():
            return jsonify({'error': f'{platform.title()} processing timeout. Please try again.'}), 400
        elif '403' in error_msg or 'forbidden' in error_msg.lower():
            return jsonify({'error': f'Access denied. This {platform} may be geo-restricted.'}), 400
        elif 'private' in error_msg.lower():
            return jsonify({'error': f'This {platform} content is private or unavailable.'}), 400
        
        return jsonify({'error': f'{platform.title()} error: {error_msg}'}), 400
        
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/api/download', methods=['POST'])
def download_video():
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        format_id = data.get('format_id')
        download_type = data.get('type', 'video')
        original_ext = data.get('ext', 'mp4')
        is_conversion = data.get('is_conversion', False)
        target_bitrate = data.get('target_bitrate', data.get('abr', 192))
        duration = data.get('duration', 0)
        platform = data.get('platform', detect_platform(url))
        
        if not url or not format_id:
            return jsonify({'error': 'URL and format_id are required'}), 400
        
        if platform == 'unknown':
            return jsonify({'error': 'Please provide a valid TikTok or Instagram URL'}), 400
        
        # Generate unique download ID
        download_id = str(uuid.uuid4())
        
        # Initialize progress
        download_progress[download_id] = {
            'status': 'initializing',
            'percent': 0,
            'platform': platform
        }
        
        print(f"Starting {platform} download - Duration: {duration}s, Type: {download_type}, Format: {format_id}")
        
        # Start download
        future = executor.submit(
            download_worker, download_id, url, format_id, download_type, 
            original_ext, is_conversion, target_bitrate, duration, platform
        )
        
        return jsonify({'download_id': download_id})
        
    except Exception as e:
        print(f"API error: {str(e)}")
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

def download_worker(download_id, url, format_id, download_type, original_ext='mp4', 
                   is_conversion=False, target_bitrate=192, duration=0, platform='tiktok'):
    """Worker function for downloading videos from TikTok and Instagram"""
    temp_dir = None
    try:
        download_progress[download_id] = {
            'status': 'starting',
            'percent': 5,
            'platform': platform
        }
        
        temp_dir = tempfile.mkdtemp()
        
        download_progress[download_id] = {
            'status': 'downloading',
            'percent': 10,
            'platform': platform
        }
        
        # TIKTOK CODE
        if platform == 'tiktok':
            base_opts = get_tiktok_ydl_opts({
                'progress_hooks': [ProgressHook(download_id)],
                'keepvideo': False,
            })
            
            if download_type == 'audio':
                ydl_opts = {
                    **base_opts,
                    'format': f'{format_id}/bestaudio',
                    'outtmpl': os.path.join(temp_dir, '%(title).100s.%(ext)s'),
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': str(target_bitrate),
                    }],
                    'prefer_ffmpeg': True,
                }
            else:
                ydl_opts = {
                    **base_opts,
                    'format': f'{format_id}/best',
                    'outtmpl': os.path.join(temp_dir, '%(title).100s.%(ext)s'),
                    'prefer_ffmpeg': True,
                }
            
            max_attempts = 3
            
        # INSTAGRAM CODE
        else:  # instagram
            base_opts = get_instagram_ydl_opts({
                'progress_hooks': [ProgressHook(download_id)],
                'keepvideo': False,
            })
            
            if download_type == 'audio':
                ydl_opts = {
                    **base_opts,
                    'format': f'{format_id}/bestaudio',
                    'outtmpl': os.path.join(temp_dir, '%(title).100s.%(ext)s'),
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': str(target_bitrate),
                    }],
                    'prefer_ffmpeg': True,
                }
            else:
                ydl_opts = {
                    **base_opts,
                    'format': f'{format_id}/best',
                    'outtmpl': os.path.join(temp_dir, '%(title).100s.%(ext)s'),
                    'prefer_ffmpeg': True,
                }
            
            max_attempts = 3
        
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                if attempt > 0:
                    print(f"{platform} retry attempt {attempt + 1} for {download_id}")
                    download_progress[download_id] = {
                        'status': f'retrying (attempt {attempt + 1})',
                        'percent': 10 + (attempt * 5),
                        'platform': platform
                    }
                    time.sleep(3 + attempt)  # Progressive backoff
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                    break  # Success, break out of retry loop
                    
            except yt_dlp.DownloadError as e:
                last_error = e
                error_msg = str(e).lower()
                
                # Handle TikTok/Instagram specific errors
                if 'private' in error_msg or 'unavailable' in error_msg:
                    raise e  # Don't retry for private/unavailable content
                elif '403' in error_msg or 'forbidden' in error_msg:
                    if attempt < max_attempts - 1:
                        continue
                    else:
                        raise e
                elif 'timeout' in error_msg or 'connection' in error_msg:
                    if attempt < max_attempts - 1:
                        continue
                    else:
                        raise e
                else:
                    if attempt < max_attempts - 1:
                        continue
                    else:
                        raise e
        else:
            # All attempts failed
            if last_error:
                raise last_error
            
        download_progress[download_id] = {
            'status': 'finalizing',
            'percent': 90,
            'platform': platform
        }
        
        # Find downloaded file
        files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
        if files:
            largest_file = max(files, key=lambda f: os.path.getsize(os.path.join(temp_dir, f)))
            filepath = os.path.join(temp_dir, largest_file)
            
            # Wait for file to be completely written
            time.sleep(1)
            
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                file_size = os.path.getsize(filepath)
                print(f"{platform} download completed: {largest_file} ({file_size/1024/1024:.1f} MB)")
                
                download_files[download_id] = {
                    'filepath': filepath,
                    'filename': largest_file,
                    'temp_dir': temp_dir,
                    'filesize': file_size,
                    'expected_format': original_ext,
                    'platform': platform
                }
                
                download_progress[download_id] = {
                    'status': 'completed',
                    'percent': 100,
                    'filename': largest_file,
                    'filepath': filepath,
                    'filesize': file_size,
                    'platform': platform
                }
            else:
                raise Exception("Downloaded file is empty or corrupted")
        else:
            raise Exception("No files were downloaded")
                
    except Exception as e:
        error_msg = str(e)
        print(f"{platform} download error for {download_id}: {error_msg}")
        
        # Platform-specific error messages
        if platform == 'tiktok':
            if 'private' in error_msg.lower():
                error_msg = "This TikTok video is private or requires login."
            elif 'unavailable' in error_msg.lower():
                error_msg = "TikTok video unavailable. It may have been deleted."
            elif '403' in error_msg or 'HTTP Error 403' in error_msg:
                error_msg = "Access denied. Video may be geo-restricted."
        else:  # instagram
            if 'private' in error_msg.lower():
                error_msg = "This Instagram content is private or requires login."
            elif 'unavailable' in error_msg.lower():
                error_msg = "Instagram content unavailable. It may have been deleted or made private."
            elif '403' in error_msg or 'HTTP Error 403' in error_msg:
                error_msg = "Access denied. Content may be geo-restricted or age-gated."
            elif 'login' in error_msg.lower():
                error_msg = "Instagram requires login for this content. Try a public post."
        
        download_progress[download_id] = {
            'status': 'error',
            'percent': 0,
            'error': f"{platform.title()} download failed: {error_msg}",
            'platform': platform
        }
        
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

@app.route('/api/progress/<download_id>', methods=['GET'])
def get_progress(download_id):
    progress = download_progress.get(download_id, {'status': 'not_found'})
    return jsonify(progress)

@app.route('/api/download-direct/<download_id>', methods=['GET'])
def download_direct(download_id):
    """Direct download endpoint for browser's default download behavior"""
    try:
        if download_id not in download_progress:
            return jsonify({'error': 'Download ID not found'}), 404
            
        progress = download_progress.get(download_id)
        
        if not progress or progress.get('status') != 'completed':
            return jsonify({
                'error': f'Download not ready. Status: {progress.get("status", "unknown") if progress else "not found"}',
                'status': progress.get('status') if progress else 'not found',
                'percent': progress.get('percent', 0) if progress else 0
            }), 400
        
        file_info = download_files.get(download_id)
        if not file_info:
            return jsonify({'error': 'File information not found'}), 404
        
        filepath = file_info['filepath']
        filename = file_info['filename']
        platform = file_info.get('platform', 'tiktok')
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found on disk'}), 404
            
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return jsonify({'error': 'Downloaded file is empty'}), 404
        
        file_ext = filename.split('.')[-1].lower()
        mime_types = {
            'mp4': 'video/mp4',
            'webm': 'video/webm', 
            'mkv': 'video/x-matroska',
            'mp3': 'audio/mpeg',
            'm4a': 'audio/mp4',
            'ogg': 'audio/ogg'
        }
        mime_type = mime_types.get(file_ext, 'application/octet-stream')
        
        def cleanup_after_send():
            # Different cleanup times for different platforms
            if platform == 'tiktok':
                cleanup_delay = 8
            else:  # instagram
                cleanup_delay = 6
                
            time.sleep(cleanup_delay)
            try:
                if os.path.exists(file_info['temp_dir']):
                    shutil.rmtree(file_info['temp_dir'], ignore_errors=True)
                download_progress.pop(download_id, None)
                download_files.pop(download_id, None)
            except Exception as e:
                print(f"Cleanup error for {download_id}: {e}")
        
        threading.Thread(target=cleanup_after_send, daemon=True).start()
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype=mime_type
        )
        
    except Exception as e:
        print(f"Direct download error for {download_id}: {str(e)}")
        return jsonify({'error': f'Download error: {str(e)}'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'TikTok & Instagram Video Downloader',
        'active_downloads': len(download_progress),
        'cached_files': len(download_files),
        'supported_platforms': ['tiktok', 'instagram']
    })

# Enhanced cleanup function
def cleanup_old_downloads():
    while True:
        try:
            current_time = time.time()
            to_remove = []
            
            for download_id in list(download_progress.keys()):
                if download_id not in download_files:
                    continue
                    
                file_info = download_files.get(download_id, {})
                temp_dir = file_info.get('temp_dir')
                platform = file_info.get('platform', 'tiktok')
                
                if temp_dir and os.path.exists(temp_dir):
                    dir_age = current_time - os.path.getctime(temp_dir)
                    
                    # Different cleanup times for different platforms
                    if platform == 'tiktok':
                        max_age = 1800   # 30 minutes for TikTok
                    else:  # instagram
                        max_age = 1200   # 20 minutes for Instagram
                    
                    if dir_age > max_age:
                        to_remove.append(download_id)
                        
            for download_id in to_remove:
                if download_id in download_files:
                    file_info = download_files[download_id]
                    if os.path.exists(file_info.get('temp_dir', '')):
                        shutil.rmtree(file_info['temp_dir'], ignore_errors=True)
                    download_files.pop(download_id, None)
                download_progress.pop(download_id, None)
                
            if to_remove:
                print(f"Cleaned up {len(to_remove)} old downloads")
                
        except Exception as e:
            print(f"Cleanup error: {e}")
        
        time.sleep(600)  # Run every 10 minutes

cleanup_thread = threading.Thread(target=cleanup_old_downloads, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    print("Starting TikTok & Instagram Video Downloader")
    print("TikTok: HD video downloads with watermark removal")
    print("Instagram: Posts, Reels, IGTV support")
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)