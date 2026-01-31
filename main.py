import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# Configure logging with rotation (1MB per file, max 5 backups)
handlers = [
    RotatingFileHandler('server.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8'),
    logging.StreamHandler(sys.stdout)
]
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=handlers
)

logging.info("Starting server initialization...")

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request, Response, Depends, Form, Body, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, JSONResponse
    from pydantic import BaseModel
    import yt_dlp
    import uvicorn
    import httpx
    import socket
    import urllib.parse
    import uuid
    import time
    import asyncio
    import secrets
    import hashlib
    import shutil
    import aiofiles # Added for async file reading
    import zipfile # Added for bulk download
    import io
    import json
    import random
    from concurrent.futures import ThreadPoolExecutor
    from typing import Dict, List, Optional, Set
    from yt_dlp.utils import sanitize_filename
    import db_utils
    # Import external downloaders
    import external_downloaders
    
    # Initialize DB
    db_utils.init_db()
    
    # Import Proxy Module
    from proxy_module import proxy_service
    
    logging.info("Dependencies imported successfully.")
except Exception as e:
    logging.critical(f"Failed to import dependencies: {e}")
    print(f"CRITICAL ERROR: Failed to import dependencies: {e}")
    sys.exit(1)


# --- Real-time Log Streaming Setup ---
log_queues: List[asyncio.Queue] = []
LOG_LOOP = None

class SSELogHandler(logging.Handler):
    """Log handler that broadcasts messages to connected SSE clients"""
    def emit(self, record):
        global LOG_LOOP
        if LOG_LOOP is None or LOG_LOOP.is_closed():
            return
        try:
            msg = self.format(record)
            # Reduce Noise: Filter common access logs
            if '"GET /system/info' in msg or '"GET /jobs' in msg or '"GET /files' in msg or '"GET /download' in msg:
                return
            
            for q in list(log_queues):
                LOG_LOOP.call_soon_threadsafe(q.put_nowait, msg)
        except Exception:
            pass

# Add this handler to root logger
sse_handler = SSELogHandler()
sse_handler.setLevel(logging.INFO)
sse_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(sse_handler)

app = FastAPI(title="yt-dlp API Server", version="8.7.3")

@app.on_event("startup")
async def startup_event():
    global LOG_LOOP
    LOG_LOOP = asyncio.get_running_loop()


# --- Middleware for Bandwidth & Fingerprinting ---
@app.middleware("http")
async def monitor_traffic(request: Request, call_next):
    try:
        if request.client:
            client_ip = request.client.host
        else:
             client_ip = "unknown"
        
        # 1. Check Blocked IP
        if client_ip != "unknown" and db_utils.is_ip_blocked(client_ip):
            return Response(content="Access Denied: Your IP is blocked.", status_code=403)

        # 2. Track Active Clients
        # Ensure active_clients is available
        if client_ip != "unknown" and 'active_clients' in globals():
            active_clients[client_ip] = time.time()
        
        # 3. Capture Request Size (Approx)
        req_size = int(request.headers.get("content-length", 0))
        
        # 4. Process Request
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # 5. Capture Response Size
        res_size = 0
        if "content-length" in response.headers:
            res_size = int(response.headers["content-length"])
        
        # 6. Log Bandwidth (if not already logged by proxy/download specific logic)
        # Note: Streaming responses might not have content-length set correctly here.
        # Proxy module handles its own logging.
        # We log here for general API usage and static files.
        if not request.url.path.startswith("/proxy") and not request.url.path.startswith("/api/download"):
             try:
                 db_utils.log_bandwidth(client_ip, req_size, res_size, "api")
             except:
                 pass

        return response
    except Exception as e:
        import traceback
        logging.error(f"Middleware Error: {e}\n{traceback.format_exc()}")
        return Response(content=f"Internal Server Error (Middleware): {e}", status_code=500, media_type="text/plain; charset=utf-8")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 実行環境のパスを取得
if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(sys.executable)
    execution_dir = os.path.dirname(sys.executable)
else:
    bundle_dir = os.path.dirname(os.path.abspath(__file__))
    execution_dir = bundle_dir

# ダウンロード保存先 (AppData)
if os.name == 'nt':
    DOWNLOAD_DIR = os.path.join(os.environ['LOCALAPPDATA'], 'YtDlpApiServer', 'downloads')
    TRASH_DIR = os.path.join(os.environ['LOCALAPPDATA'], 'YtDlpApiServer', 'trash')
else:
    DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), ".YtDlpApiServer", "downloads")
    TRASH_DIR = os.path.join(os.path.expanduser("~"), ".YtDlpApiServer", "trash")

# Temp directory for processing
TEMP_DIR = os.path.join(os.path.dirname(DOWNLOAD_DIR), 'temp')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

if os.path.exists(DOWNLOAD_DIR):
    app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204) # No content

# --- Auth & Stats ---
AUTH_COOKIE_NAME = "ytdlp_auth"

# Session Store (Simple in-memory)
sessions: Dict[str, Dict] = {}

# Environment Check for Cookie Security
# Defaults to False for easier local dev, set YTDLP_ENV=production for secure
IS_PRODUCTION = os.environ.get('YTDLP_ENV') == 'production'

# Rate Limiting & Limits
# user_usage = { username: { 'download': [timestamps], 'proxy': [timestamps] } }
user_usage: Dict[str, Dict[str, List[float]]] = {}

LIMITS = {
    'user': { # Shared account
        'download_limit': 1, # per hour
        'proxy_limit': 0,    # per hour (disabled)
        'speed_limit': 0.8,  # MB/s (800KB/s)
        'session_duration': 86400 * 1 # 1 day
    },
    'personal': { # Created via registration
        'download_limit': 5, # per hour (includes playlist)
        'proxy_limit': 50,
        'speed_limit': 2.0,  # MB/s
        'session_duration': 86400 * 7 # 7 days
    },
    'admin': {
        'download_limit': 9999,
        'proxy_limit': 9999,
        'speed_limit': 0, # Unlimited
        'session_duration': 86400 * 30 # 30 days
    }
}

# Notifications Store
# username -> list of notification dicts { "id": str, "message": str, "type": "info"|"error"|"success", "timestamp": float }
user_notifications: Dict[str, List[Dict]] = {}

def add_notification(username: str, message: str, type: str = "info"):
    if username not in user_notifications:
        user_notifications[username] = []
    user_notifications[username].append({
        "id": str(uuid.uuid4()),
        "message": message,
        "type": type,
        "timestamp": time.time()
    })

def check_rate_limit(username: str, role: str, action: str) -> bool:
    if role == 'admin': return True
    
    # Determine limit type
    limit_key = 'personal' if role != 'user' else 'user' # 'user' role is the shared account
    if action == 'download':
        limit = LIMITS[limit_key]['download_limit']
    elif action == 'proxy':
        limit = LIMITS[limit_key]['proxy_limit']
    else:
        return True
        
    now = time.time()
    if username not in user_usage:
        user_usage[username] = {'download': [], 'proxy': []}
    
    # Clean old timestamps (older than 1h)
    user_usage[username][action] = [t for t in user_usage[username][action] if now - t < 3600]
    
    if len(user_usage[username][action]) >= limit:
        return False
    
    return True

def add_rate_limit_usage(username: str, action: str):
    if username not in user_usage:
        user_usage[username] = {'download': [], 'proxy': []}
    user_usage[username][action].append(time.time())

# Stats
active_clients: Dict[str, float] = {} # IP -> Last Access Timestamp
MAX_CLIENTS = 50 # Increased from 3 to 50 to avoid strict blocking
CLIENT_TIMEOUT = 300 # 5 minutes

def get_active_client_count():
    now = time.time()
    # Cleanup old clients
    to_remove = [ip for ip, last_seen in active_clients.items() if now - last_seen > CLIENT_TIMEOUT]
    for ip in to_remove:
        del active_clients[ip]
    return len(active_clients)

def check_auth(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in sessions:
        return False
    
    session = sessions[token]
    if session['exp'] < time.time():
        del sessions[token]
        return False
        
    return True

# Security Headers Middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    
    # For proxy routes, completely disable all security policies
    if request.url.path.startswith("/proxy") or request.url.path.startswith("/api/proxy"):
        # Remove any security headers that could block content (check both cases)
        headers_to_remove_lower = {
            "content-security-policy",
            "content-security-policy-report-only",
            "x-content-security-policy",
            "x-webkit-csp",
            "x-frame-options",
            "x-xss-protection",
            "permissions-policy",
            "cross-origin-embedder-policy",
            "cross-origin-opener-policy",
            "cross-origin-resource-policy",
            "strict-transport-security",
            "referrer-policy"
        }
        # Build list of keys to delete (can't modify dict during iteration)
        keys_to_delete = [k for k in response.headers.keys() if k.lower() in headers_to_remove_lower]
        for k in keys_to_delete:
            del response.headers[k]
        return response
    
    # For non-proxy routes, set permissive CSP
    response.headers["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:; script-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:; connect-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:; img-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:; frame-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:; style-src * 'unsafe-inline' 'unsafe-eval' data: blob: chrome-extension:;"
    # Remove Permissions-Policy to avoid "Unrecognized feature" errors
    keys_to_delete = [k for k in response.headers.keys() if k.lower() == "permissions-policy"]
    for k in keys_to_delete:
        del response.headers[k]
    return response

# Middleware for Auth & Load Limit
@app.middleware("http")
async def auth_and_limit_middleware(request: Request, call_next):
    try:
        # Allow static resources, login endpoints, and favicon
        if request.url.path.startswith("/static") or \
           request.url.path == "/favicon.ico" or \
           request.url.path in ["/login", "/api/login", "/system/info", "/api/auth/register", "/api/client/handshake", "/download"]:
            return await call_next(request)
        
        # Check Auth
        if not check_auth(request):
            # If API request, return 401
            if request.url.path.startswith("/api"):
                 return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            # If page request, redirect to login
            return RedirectResponse("/static/login.html")

        client_ip = request.client.host if request.client else "unknown"
        
        # Check Blocked IP
        if client_ip != "unknown" and db_utils.is_ip_blocked(client_ip):
            return JSONResponse(status_code=403, content={"detail": "Access Denied: Your IP is blocked."})

        # Check Role for Bypass
        token = request.cookies.get(AUTH_COOKIE_NAME)
        is_admin = False
        if token and token in sessions and sessions[token].get('role') == 'admin':
            is_admin = True

        # Check Load Limit (for new sessions or heavy endpoints)
        # Exclude Proxy from Load Limit to prevent blocking Admin due to proxy errors
        if not request.url.path.startswith("/proxy") and not request.url.path.startswith("/api/proxy"):
            if client_ip != "unknown":
                active_clients[client_ip] = time.time()
            
            # Bypass for Admin
            if not is_admin and get_active_client_count() > MAX_CLIENTS:
                 # Check if this specific IP was already active (it is, we just updated it)
                 # We need to know if it's a *new* client pushing us over.
                 # For simplicity, if total > MAX, we reject. 
                 # This might block existing users if a 4th one spams. 
                 # Better: Track "session start" time.
                 return Response(content="現在アクセスが集中しているため、サーバー負荷軽減のためアクセスを制限しています", status_code=503, media_type="text/plain; charset=utf-8")

        response = await call_next(request)
        return response
    except Exception as e:
        import traceback
        logging.error(f"Auth Middleware Error: {e}\n{traceback.format_exc()}")
        return Response(content=f"Internal Server Error (Auth Middleware): {e}", status_code=500, media_type="text/plain; charset=utf-8")

# ffmpeg設定
ffmpeg_paths = [
    os.path.join(bundle_dir, "ffmpeg.exe"),
    os.path.join(execution_dir, "bin", "ffmpeg.exe"),
    os.path.join(execution_dir, "ffmpeg.exe"),
]

# Add AppData path
if os.name == 'nt':
    appdata = os.environ.get('LOCALAPPDATA')
    if appdata:
        ffmpeg_paths.append(os.path.join(appdata, "YtDlpApiServer", "bin", "ffmpeg.exe"))

ffmpeg_found = False
for path in ffmpeg_paths:
    if os.path.exists(path):
        logging.info(f"Found ffmpeg at: {path}")
        ffmpeg_dir = os.path.dirname(path)
        os.environ["PATH"] += os.pathsep + ffmpeg_dir
        ffmpeg_found = True
        break

if not ffmpeg_found:
    logging.warning("ffmpeg not found in bundled or execution directories. Relying on system PATH.")

# --- Background Tasks ---

def cleanup_old_files():
    """Delete files older than 3 days in DOWNLOAD_DIR"""
    try:
        now = time.time()
        days_3 = 3 * 24 * 3600
        
        if os.path.exists(DOWNLOAD_DIR):
            for f in os.listdir(DOWNLOAD_DIR):
                fp = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(fp):
                    try:
                        stat = os.stat(fp)
                        # Use modification time
                        if now - stat.st_mtime > days_3:
                            logging.info(f"Deleting old file: {f}")
                            os.remove(fp)
                    except Exception as e:
                        logging.error(f"Error deleting old file {f}: {e}")
                        
        logging.info("Cleanup completed")
    except Exception as e:
        logging.error(f"Cleanup failed: {e}")

@app.on_event("startup")
async def startup_event():
    # Run cleanup on startup
    executor.submit(cleanup_old_files)

class JobStatus:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    FINISHED = "finished"
    ERROR = "error"

class DownloadJob(BaseModel):
    id: str
    url: str
    status: str
    progress: float = 0
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    title: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: float
    # Metadata
    client_ip: Optional[str] = None
    username: Optional[str] = None
    client_id: Optional[str] = None

# In-memory job store
jobs: Dict[str, DownloadJob] = {}

# Thread pool for concurrent downloads (Limit to 2)
executor = ThreadPoolExecutor(max_workers=2)

class DownloadRequest(BaseModel):
    url: str
    type: str = "video" # video, audio
    quality: str = "720" # best, 1080, 720, 480
    audio_format: str = "mp3" # mp3, m4a, wav
    subtitles: bool = True
    subtitles_lang: str = "ja"
    embed_subtitles: bool = True

def progress_hook(d, job_id):
    """yt-dlp progress hook"""
    if d['status'] == 'downloading':
        job = jobs.get(job_id)
        if job:
            # Calculate progress percentage
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                job.progress = round((downloaded / total) * 100, 1)
            
            job.status = JobStatus.DOWNLOADING
            job.speed = d.get('_speed_str')
            job.eta = d.get('_eta_str')
            job.filename = os.path.basename(d.get('filename', ''))
            
    elif d['status'] == 'finished':
        job = jobs.get(job_id)
        if job:
            job.progress = 100
            job.status = JobStatus.FINISHED
            job.filename = os.path.basename(d.get('filename', ''))
            
            # Log successful download
            details = f"Download Finished: {job.title or job.url} ({job.filename})"
            if job.username:
                 details += f" User: {job.username}"
                 # Notify user
                 msg = f"ダウンロードが完了しました: {job.title or job.filename}"
                 add_notification(job.username, msg, "success")
            
            # Notify admin if heavy/long download (simple heuristic: if it took > 10 mins or size > 1GB?)
            # Since we don't track duration easily here without start time, let's just notify admin for every completion or errors
            # Or assume explicit requirement: "download complete notification"
            
            if job.client_id:
                 details += f" CID: {job.client_id}"
            
            db_utils.log_event(job.client_ip or "unknown", "DOWNLOAD", details)

def run_download(job_id: str, req: DownloadRequest):
    """Execute download in thread pool"""
    job = jobs.get(job_id)
    if not job:
        return

    # Determine rate limit based on user role
    limit_rate = None
    if job.username:
        # Resolve role. We don't have role stored in job, but we can infer or pass it.
        # Ideally job should store role.
        # For now, let's query DB or cache?
        # Or just use the default logic: if username == 'user' -> user, 'admin' -> admin, else personal
        if job.username == 'admin':
            role = 'admin'
        elif job.username == 'user':
            role = 'user'
        else:
            role = 'personal'
            
        limit_mb = LIMITS.get(role, {}).get('speed_limit', 0)
        if limit_mb > 0:
            limit_rate = int(limit_mb * 1024 * 1024) # to bytes
            
    logging.info(f"Starting job {job_id}: {req.url} (Limit: {limit_rate})")
    
    # Use TEMP_DIR for downloading
    # Use job_id as filename to avoid ambiguity and encoding issues during download
    ydl_opts = {
        'outtmpl': os.path.join(TEMP_DIR, f'{job_id}.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: progress_hook(d, job_id)],
        'writethumbnail': False,
        'restrictfilenames': True, 
        'windowsfilenames': True,
        'noplaylist': False,
        # Improve stability
        'cachedir': False, 
        'nocheckcertificate': True,
        # 'extractor_args': {'youtube': {'player_client': ['tv']}},
    }
    
    # Cookie handling: Prioritize cookies.txt
    cookies_path = os.path.join(execution_dir, 'cookies.txt')
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path
    
    # NOTE: "cookiesfrombrowser" removed to prevent errors on servers/services without browser profiles.
    # Users must provide cookies.txt if cookies are needed.
    
    if limit_rate:
        ydl_opts['ratelimit'] = limit_rate

    # Subtitle options
    if req.subtitles:
        ydl_opts.update({
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [req.subtitles_lang],
            'embedsubtitles': req.embed_subtitles,
        })

    # Check for cookies.txt (Overrides browser cookies)
    cookie_file = os.path.join(execution_dir, "cookies.txt")
    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file
        logging.info(f"Using cookies from {cookie_file}")
        if 'cookiesfrombrowser' in ydl_opts:
            del ydl_opts['cookiesfrombrowser'] # Priority to file

    # Playlist handling logic
    if "playlist?list=" in req.url:
        logging.info(f"Explicit playlist URL detected: {req.url}. Enabling playlist mode (limit 10).")
        ydl_opts['noplaylist'] = False
        ydl_opts['ignoreerrors'] = True
        ydl_opts['playlistend'] = 10
        # For playlist, we need unique filenames
        ydl_opts['outtmpl'] = os.path.join(TEMP_DIR, f'{job_id}_%(playlist_index)s.%(ext)s')
    elif "list=" in req.url:
        logging.info(f"URL contains list parameter but treated as single video: {req.url}")

    # Locate FFmpeg to ensure merging works
    ffmpeg_path = None
    possible_ffmpeg_paths = [
        os.path.join(execution_dir, 'ffmpeg.exe'),
        os.path.join(execution_dir, 'bin', 'ffmpeg.exe'),
        os.path.join(execution_dir, 'release', 'ffmpeg.exe'), # Check release folder
        "ffmpeg" # System path fallback
    ]
    for path in possible_ffmpeg_paths:
        if path == "ffmpeg" or os.path.exists(path):
            ffmpeg_path = path
            break
            
    if ffmpeg_path and ffmpeg_path != "ffmpeg":
         ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path)

    # Format selection
    if req.type == 'audio':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': req.audio_format,
            'preferredquality': '192',
        }]
    else:
        # Video format - Try best quality merge first, then best single file, then separate bests
        # If ffmpeg is missing, merge will fail, but /best should handle it fallback
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
        ydl_opts['format_sort'] = ['res', 'ext:mp4:m4a']

    try:
        # Wrapper to allow retry logic
        def attempt_download(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                 return ydl.extract_info(req.url, download=True)

        try:
            info = attempt_download(ydl_opts)
        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e)
            
            if "Requested format is not available" in err_msg:
                logging.warning("Requested format unavailable. Retrying with generic 'best' format...")
                ydl_opts['format'] = 'best'
                if 'format_sort' in ydl_opts:
                    del ydl_opts['format_sort']
                info = attempt_download(ydl_opts)

            elif ("Sign in to confirm" in err_msg or "downloaded file is empty" in err_msg) and 'cookiefile' in ydl_opts:
                logging.warning(f"Download error detected ({err_msg}). Retrying with browser cookies (Chrome/Edge)...")
                # Fallback: Remove file and use browser. Removed Firefox to avoid keyring issues.
                try: 
                    # Only attempt if not running as system/service to avoid crash
                    if 'systemprofile' not in os.path.expanduser('~').lower():
                        del ydl_opts['cookiefile']
                        ydl_opts['cookiesfrombrowser'] = ('chrome', 'edge')
                        info = attempt_download(ydl_opts)
                    else:
                        logging.error("Cannot use browser cookies in system profile. Please check cookies.txt.")
                        raise e
                except Exception as ex:
                    logging.error(f"Browser cookie fallback failed: {ex}")
                    raise e
            else:
                raise e
            
        # Update title from final info
        channel_name = 'UnknownChannel'
        if info:
            job.title = info.get('title', job.title)
            channel_name = info.get('channel', 'UnknownChannel')

        # Find the downloaded file(s)
        found_files = []
        if os.path.exists(TEMP_DIR):
            for f in os.listdir(TEMP_DIR):
                # Check for job_id prefix
                # Exclude temp files
                if f.startswith(job_id) and not f.endswith('.part') and not f.endswith('.ytdl'):
                    found_files.append(os.path.join(TEMP_DIR, f))
        
        if not found_files:
            logging.warning(f"No files found for job {job_id} in {TEMP_DIR}")
            job.error_msg = "Download finished but file not found"
            job.status = JobStatus.ERROR
            return

        # Move files to DOWNLOAD_DIR with correct name
        from yt_dlp.utils import sanitize_filename
        
        final_filenames = []
        for file_path in found_files:
            ext = os.path.splitext(file_path)[1]
            
            # Sanitize title and channel
            safe_title = sanitize_filename(job.title)
            safe_channel = sanitize_filename(channel_name)
            
            # Construct Desired Filename: Channel - Title
            # If channel is missing, just use title
            if safe_channel:
                base_name = f"{safe_channel} - {safe_title}"
            else:
                base_name = safe_title
            
            if len(found_files) > 1:
                # Try to extract index from filename if possible
                fname = os.path.basename(file_path)
                try:
                    # job_id_1.mp4 -> 1
                    idx_part = fname.replace(job_id + '_', '').split('.')[0]
                    new_filename = f"{base_name}_{idx_part}{ext}"
                except:
                    new_filename = f"{base_name}_{os.path.basename(file_path)}{ext}"
            else:
                new_filename = f"{base_name}{ext}"

            dest_path = os.path.join(DOWNLOAD_DIR, new_filename)
            
            # Handle collision
            counter = 1
            base_dest = os.path.splitext(dest_path)[0]
            while os.path.exists(dest_path):
                dest_path = f"{base_dest}_{counter}{ext}"
                counter += 1
            
            shutil.move(file_path, dest_path)
            final_filenames.append(os.path.basename(dest_path))
            logging.info(f"Moved {file_path} to {dest_path}")

        job.filename = final_filenames[0] # Set the first one as main
        job.status = JobStatus.FINISHED
        logging.info(f"Job {job_id} completed. Filename: {job.filename}")
        
        # Record owner
        if job.username:
                db_utils.add_file_owner(job.filename, job.username)
                # Handle bulk parts if distinct? Assuming single file or playlist.
                for fname in final_filenames[1:]:
                    db_utils.add_file_owner(fname, job.username)
        
    except Exception as e:
        # Fallback attempt
        logging.error(f"yt-dlp failed: {e}. Attempting Fallback...")
        
        # Since we are in a ThreadPoolExecutor, we need to spin up a new event loop for async fallback
        try:
             fallback_info = asyncio.run(attempt_fallback_download(req.url, job_id))
        except Exception as af_e:
             logging.error(f"Fallback async execution failed: {af_e}")
             fallback_info = None

        if fallback_info:
            logging.info("Fallback download successful. Processing file...")
            # Simulate info dict
            info = {'title': fallback_info['title']} 
            channel_name = "ExternalSource"
            # Re-run file find and move logic
            # This is duplicate code but cleanest without Refactoring entire function now
            found_files = []
            if os.path.exists(TEMP_DIR):
                for f in os.listdir(TEMP_DIR):
                    if f.startswith(job_id):
                        found_files.append(os.path.join(TEMP_DIR, f))
            
            if found_files:
                 from yt_dlp.utils import sanitize_filename
                 safe_title = sanitize_filename(info['title'])
                 # Move Logic
                 dest_path = os.path.join(DOWNLOAD_DIR, safe_title + "." + fallback_info['ext'])
                 counter = 1
                 while os.path.exists(dest_path):
                     dest_path = os.path.join(DOWNLOAD_DIR, f"{safe_title}_{counter}.{fallback_info['ext']}")
                     counter += 1
                 
                 shutil.move(found_files[0], dest_path)
                 job.filename = os.path.basename(dest_path)
                 job.status = JobStatus.FINISHED
                 job.title = info['title']
                 if job.username: db_utils.add_file_owner(job.filename, job.username)
                 return

        job.status = JobStatus.ERROR
        job.error_msg = f"Download Failed: {str(e)} (And fallback failed)"
        logging.error(f"Job {job_id} completely failed.")

async def attempt_fallback_download(url: str, job_id: str):
    """Fallback using multiple Cobalt API providers in parallel (Race)"""
    logging.info(f"Using Fallback Chain for {job_id}")
    job = jobs.get(job_id)
    if not job: return False

    headers = {
        "Accept": "application/json", 
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    }

    # Parallel check for fastest instance
    # We include traditional Cobalt instances AND external scrapers (SaveFrom, Y2Mate)
    # This ensures maximum robustness and speed.
    
    cobalt_instances = [
        "https://api.cobalt.tools",
        "https://cobalt.tools",
        "https://co.wuk.sh",
        "https://api.wuk.sh",
        "https://nyc1.coapi.ggtyler.dev",
        "https://cal1.coapi.ggtyler.dev", 
        "https://par1.coapi.ggtyler.dev",
        "https://coapi.kelig.me",
        "https://ca.haloz.at",
        "https://cobalt-api.ayo.tf",
        "https://api.cobalt.tacohitbox.com",
        "https://cobalt.twi.sh",
        "https://api.sukka.moe/cobalt",
        "https://cobalt.kwiatekmiki.com",
        "https://cobalt.q1.app",
        "https://cobalt.synced.sh",
        "https://c.jaops.org"
    ]

    async def check_instance(client, base_url):
        try:
            payload = {"url": url}
            # Try V7
            api_url = f"{base_url.rstrip('/')}/api/json"
            try:
                resp = await client.post(api_url, json=payload, headers=headers)
            except:
                # Try Root if V7 network fails immediately
                api_url = f"{base_url.rstrip('/')}/"
                try:
                    resp = await client.post(api_url, json={"url": url}, headers=headers)
                except:
                    return None

            if resp.status_code not in [200, 201]:
                # Retry Root if V7 returned 404
                if api_url.endswith("/api/json"):
                     api_url = f"{base_url.rstrip('/')}/"
                     try:
                        resp = await client.post(api_url, json={"url": url}, headers=headers)
                     except: pass
            
            if resp.status_code not in [200, 201]:
                return None
                
            try:
                data = resp.json()
            except:
                return None
            
            if data.get('status') == 'error':
                 return None
                 
            # Extract
            download_url = None
            status = data.get('status')
            if status in ['tunnel', 'redirect']:
                download_url = data.get('url')
            elif status == 'picker' and data.get('picker'):
                download_url = data['picker'][0].get('url')
            elif 'url' in data: 
                download_url = data.get('url')
                
            if download_url:
                return {'base_url': base_url, 'data': data, 'download_url': download_url, 'source': 'Cobalt'}
        except:
            pass
        return None

    # Use AsyncClient for parallel requests
    # verify=False to avoid SSL issues on some shady instances
    async with httpx.AsyncClient(timeout=25.0, verify=False) as client:
        # Create tasks for Cobalt
        tasks = [check_instance(client, u) for u in cobalt_instances]
        
        # Add external scrapers tasks
        tasks.append(external_downloaders.get_savefrom(url, client))
        tasks.append(external_downloaders.get_y2mate(url, client))
        
        # Log count
        # logging.info(f"Racing {len(tasks)} fallback providers...")
        
        for future in asyncio.as_completed(tasks):
            res = await future
            if res:
                source = res.get('source', 'Unknown')
                base_url = res.get('base_url', 'External')
                download_url = res.get('download_url')
                data = res.get('data', {})
                
                logging.info(f"Fallback Winner: {source} ({base_url})")
                
                # Filename hint
                f_hint = data.get('filename', f'{source}_{job_id}.mp4')
                ext = f_hint.split('.')[-1] if '.' in f_hint else 'mp4'
                
                # Download File
                return await process_generic_download_async(download_url, job_id, client, f_hint, ext)
            
    # All fallbacks failed
    logging.error("All fallback instances/scrapers failed.")

async def process_generic_download_async(download_url, job_id, client, filename_hint, ext):
    """Helper to download file from a direct link (Async)"""
    # Create temp path
    # If generic, we default to mp4
    temp_path = os.path.join(TEMP_DIR, f"{job_id}.{ext}")
    job = jobs.get(job_id)
    
    try:
        async with client.stream("GET", download_url) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            with open(temp_path, 'wb') as f:
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and job:
                        job.progress = round((downloaded / total) * 100, 1)
        
        return {'title': filename_hint, 'ext': ext}
    except Exception as e:
        logging.error(f"Generic Download Failed for {job_id}: {e}")
        return False

# --- API Endpoints ---


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    """Stream server logs via Server-Sent Events"""
    q = asyncio.Queue()
    log_queues.append(q)
    try:
        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    # Wait for new log
                    data = await q.get()
                    yield f"data: {data}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                if q in log_queues:
                    log_queues.remove(q)

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    except Exception:
        if q in log_queues:
            log_queues.remove(q)
        return Response("Stream Error", status_code=500)

@app.get("/")
async def index():
    return FileResponse(os.path.join("static", "index.html"))

@app.get("/quiz")
async def quiz_page():
    return FileResponse(os.path.join("static", "quiz.html"))

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type='application/octet-stream', filename=filename)

@app.get("/api/stream")
async def stream_video(url: str, request: Request):
    """
    Get direct stream URL from yt-dlp and proxy it.
    """
    try:
        ydl_opts = {
            # Prioritize progressive HTTP streams (mp4) which are playable in browser <video>
            'format': 'best[protocol^=http][ext=mp4]/best[protocol^=http]/best[ext=mp4]/best',
            'quiet': True,
            'cachedir': False,
            'force_ipv4': True, # Prioritize IPv4 for extraction
            'source_address': '0.0.0.0', # Force binding to IPv4 interface
            # 'extractor_args': {'youtube': {'player_client': ['tv']}},
        }
        
        # Cookie handling
        cookies_path = os.path.join(execution_dir, 'cookies.txt')
        if os.path.exists(cookies_path):
            ydl_opts['cookiefile'] = cookies_path
        
        # Note: Do not use cookiesfrombrowser here to avoid system profile errors.
        
        # Determine Speed Limit
        token = request.cookies.get(AUTH_COOKIE_NAME)
        role = "guest"
        if token and token in sessions:
            role = sessions[token].get('role', 'user')
        
        limit_mb = LIMITS.get(role, {}).get('speed_limit', 0)
        limit_bps = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get('url')
            if not stream_url:
                raise Exception("No stream URL found")
            
            # Proxy the stream
            # We use the proxy_service logic to utilize the speed limiter (stream_response)
            # But stream_response takes an httpx.Response object.
            # We need to make the request using proxy_service.client (or similar) or create options.
            # proxy_service.client is pre-configured.
            
            # Since stream_response closes the response, we should be careful.
            
            # Use headers from yt-dlp info if available, or default
            headers = info.get('http_headers', {})
            # Ensure User-Agent is set if missing
            if 'User-Agent' not in headers:
                 headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

            # Forward Range Header
            range_header = request.headers.get('range')
            if range_header:
                headers['Range'] = range_header

            # FORCE IPv4 Connection for httpx to match yt-dlp's IPv4 extraction
            try:
                parsed_url = urllib.parse.urlparse(stream_url)
                hostname = parsed_url.hostname
                port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
                
                # Resolve to IPv4
                addr_info = socket.getaddrinfo(hostname, port, socket.AF_INET, socket.SOCK_STREAM)
                if addr_info:
                    ip_addr = addr_info[0][4][0]
                    # Replace hostname with IP in URL logic
                    # httpx does not have a clean way to force IP without Host header manipulation
                    stream_url = stream_url.replace(hostname, ip_addr, 1)
                    headers['Host'] = hostname # Ensure SNI/Host matches original
                    # logging.info(f"Forced IPv4 resolution: {hostname} -> {ip_addr}")
            except Exception as e:
                logging.warning(f"Failed to force IPv4 resolution: {e}")

            client = httpx.AsyncClient(verify=False, follow_redirects=True)
            req_stream = client.build_request("GET", stream_url, headers=headers)
            r = await client.send(req_stream, stream=True)
            
            msg = f"Proxying Stream: {stream_url[:50]}... Status: {r.status_code} Type: {r.headers.get('content-type')}"
            logging.info(msg)
            
            response_headers = {}
            for k in ['Content-Range', 'Content-Length', 'Accept-Ranges', 'Content-Type']:
                if r.headers.get(k):
                    response_headers[k] = r.headers.get(k)
            
            return StreamingResponse(
                proxy_service.stream_response(r, request.client.host, limit_bps),
                status_code=r.status_code,
                headers=response_headers,
                media_type=r.headers.get("content-type"),
            )
    except Exception as e:
        logging.error(f"Stream error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/download")
async def start_download(request: DownloadRequest, req: Request):
    job_id = str(uuid.uuid4())
    
    # Metadata extraction
    username = None
    role = "user"
    token = req.cookies.get(AUTH_COOKIE_NAME)
    if token:
         sess = sessions.get(token)
         if sess:
             username = sess.get('username')
             role = sess.get('role', 'user')
    
    # Rate Limit Check
    if username:
        if not check_rate_limit(username, role, 'download'):
             raise HTTPException(status_code=429, detail="API Limit Exceeded: Download quota reached for this hour.")
        add_rate_limit_usage(username, 'download')

    client_id = req.cookies.get('CLIENT_ID')
    
    job = DownloadJob(
        id=job_id,
        url=request.url,
        status=JobStatus.QUEUED,
        created_at=time.time(),
        client_ip=req.client.host,
        username=username,
        client_id=client_id
    )
    jobs[job_id] = job
    
    # Submit to thread pool
    executor.submit(run_download, job_id, request)
    
    return {"job_id": job_id, "message": "Queued"}

@app.get("/jobs", response_model=List[DownloadJob])
async def list_jobs():
    return list(jobs.values())

@app.get("/jobs/{job_id}", response_model=DownloadJob)
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    if job_id in jobs:
        del jobs[job_id]
    return {"message": "Deleted"}

@app.get("/files", response_model=List[Dict])
async def list_files(request: Request):
    # Identify User
    token = request.cookies.get(AUTH_COOKIE_NAME)
    username = None
    role = "guest"
    if token and token in sessions:
        sess = sessions[token]
        username = sessions[token].get('username') # No longer just sessions[token] as it is a dict
        # Wait, session was saved as { "exp", "role", "ip" } in login...
        # Wait, I need to check login implementation.
        # Login stores: "role", "ip", "exp". It does NOT store username.
        # Let's check login again.
        # Line 725: sessions[session_token] = { "exp": ..., "role": role, "ip": ... }
        # I need to store username in session too.
        pass

    # Quick fix: Add username to session in login
    
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        file_owners = db_utils.get_file_owners()
        
        # Determine effective role/username
        if token and token in sessions:
            sess = sessions[token]
            role = sess.get('role', 'guest')
            # I need username.
            # I will modify login to store username.
            # But for now, let's assume I can get it.
            # Actually, I should update login now.
            username = sess.get('username')
            
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp):
                try:
                    stat = os.stat(fp)
                    owner = file_owners.get(f)
                    
                    # Filtering Logic
                    is_visible = False
                    
                    if role == 'admin':
                        is_visible = True
                    elif owner == 'user' or owner is None:
                        is_visible = True
                    elif username and owner == username:
                        is_visible = True
                    
                    if is_visible:
                        files.append({
                            "filename": f,
                            "size": stat.st_size,
                            "created_at": stat.st_ctime,
                            "owner": owner
                        })
                except Exception:
                    pass
    # Sort by newest
    files.sort(key=lambda x: x["created_at"], reverse=True)
    return files

class BulkFileRequest(BaseModel):
    filenames: List[str]

@app.post("/api/files/bulk_delete")
async def bulk_delete_files(req: BulkFileRequest):
    deleted = []
    errors = []
    if not os.path.exists(TRASH_DIR):
        os.makedirs(TRASH_DIR)
        
    for filename in req.filenames:
        safe_name = sanitize_filename(filename)
        file_path = os.path.join(DOWNLOAD_DIR, safe_name)
        if os.path.exists(file_path):
            try:
                # Move to trash
                trash_path = os.path.join(TRASH_DIR, safe_name)
                if os.path.exists(trash_path):
                     base, ext = os.path.splitext(safe_name)
                     trash_path = os.path.join(TRASH_DIR, f"{base}_{int(time.time())}{ext}")
                shutil.move(file_path, trash_path)
                # Remove from DB
                db_utils.remove_file_owner(filename)
                deleted.append(filename)
            except Exception as e:
                errors.append(f"{filename}: {e}")
        else:
            errors.append(f"{filename}: Not found")
    
    return {"deleted": deleted, "errors": errors}

@app.post("/api/files/bulk_download")
async def bulk_download_files(req: BulkFileRequest):
    temp_zip = os.path.join(TEMP_DIR, f"bulk_{uuid.uuid4()}.zip")
    try:
        with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in req.filenames:
                safe_name = sanitize_filename(filename)
                file_path = os.path.join(DOWNLOAD_DIR, safe_name)
                if os.path.exists(file_path):
                    zf.write(file_path, arcname=filename)
        
        def iterfile():
            with open(temp_zip, mode="rb") as file_like:
                yield from file_like
            try:
                os.remove(temp_zip)
            except:
                pass

        return StreamingResponse(iterfile(), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=downloads.zip"})
    except Exception as e:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/files/{filename}")
async def delete_file(filename: str):
    fp = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(fp):
        try:
            os.remove(fp)
            return {"message": "Deleted"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="File not found")

# --- Auth Endpoints ---

class LoginRequest(BaseModel):
    username: str
    password: str
    is_pwa: bool = False

class RegisterRequest(BaseModel):
    nickname: str
    password: str
    ua: str
    screen: str

class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    username: Optional[str] = None
    nickname: Optional[str] = None

class ClientInfo(BaseModel):
    user_agent: str
    screen_res: str
    window_size: str
    color_depth: int
    theme: Optional[str] = None
    orientation: Optional[str] = None
    device_name: Optional[str] = "Unknown"
    client_id: Optional[str] = None

@app.post("/api/client/handshake")
async def client_handshake(info: ClientInfo, request: Request, response: Response):
    # Determine Client ID
    client_id = info.client_id
    if not client_id or client_id == 'null' or client_id == 'undefined':
        # Check cookie
        client_id = request.cookies.get('CLIENT_ID')
        if not client_id:
             client_id = str(uuid.uuid4())[:12]
    
    # Check Username
    token = request.cookies.get(AUTH_COOKIE_NAME)
    username = None
    if token:
        session = sessions.get(token)
        if session:
            username = session.get('username')
    
    # Log Session Start (Detailed)
    log_msg = f"Session Start: User={username or 'Guest'}, ID={client_id}, Device={info.device_name}, Screen={info.screen_res}, UA={info.user_agent}"
    logging.info(log_msg)
    
    # Update DB
    db_info = {
        "ua": info.user_agent,
        "screen": info.screen_res,
        "window": info.window_size,
        "depth": info.color_depth,
        "theme": info.theme,
        "orientation": info.orientation,
        "device_name": info.device_name
    }
    db_utils.update_client_info(client_id, request.client.host, db_info, username)
    
    # Set Cookies (Long lived)
    response.set_cookie(key='CLIENT_ID', value=client_id, max_age=31536000, httponly=False) 
    if username:
        response.set_cookie(key='USERNAME', value=username, max_age=31536000, httponly=False)
    
    return {"client_id": client_id, "username": username}

@app.post("/api/login")
async def login(req: LoginRequest, response: Response, request: Request):
    try:
        user = db_utils.verify_user(req.username, req.password)
        if user:
            # Generate Session
            session_token = str(uuid.uuid4())
            role = user.get('role', 'user')
            if role == 'pending':
                 raise HTTPException(status_code=403, detail="承認待ちのアカウントです")
            
            # Safe role lookup
            if role not in LIMITS:
                role = 'user'
                 
            max_age = LIMITS[role]['session_duration']
            
            # Concurrent Login Check
            tokens_to_remove = [k for k, v in sessions.items() if v.get('username') == req.username]
            for t in tokens_to_remove:
                del sessions[t]

            sessions[session_token] = {
                'username': req.username,
                'role': role,
                'exp': time.time() + max_age
            }

            db_utils.log_event(request.client.host, "LOGIN_SUCCESS", f"User: {req.username}")

            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=session_token,
                httponly=True,
                secure=IS_PRODUCTION, # Allow use in IFrames/Cross-site if needed, but requires HTTPS
                samesite='Lax', # Modern Browser default, prevents CSRF but allows top-level nav
                max_age=max_age
            )
            return {"message": "Logged in", "role": role}
        else:
            db_utils.log_event(request.client.host, "LOGIN_FAILED", f"User: {req.username}")
            # Check if username exists to give hint
            if db_utils.check_username_exists(req.username):
                 raise HTTPException(status_code=401, detail="パスワードが違います。忘れた場合は管理者へ連絡してください。")
            raise HTTPException(status_code=401, detail="認証に失敗しました")
    except Exception as e:
        logging.error(f"Login Error: {e}")
        raise HTTPException(status_code=500, detail=f"Login Handler Error: {str(e)}")

@app.post("/api/auth/register")
async def register(req: RegisterRequest, request: Request):
    nickname = req.nickname.strip()
    password = req.password.strip()
    
    # Check existing
    if db_utils.check_username_exists(nickname):
         raise HTTPException(status_code=400, detail="この名前は既に使用されています。ログインするか、別の名前を使用してください。")
    
    if len(nickname) < 3:
         raise HTTPException(status_code=400, detail="ユーザー名は3文字以上にしてください")
         
    # Password Policy: 4-20 alphanumeric
    if not (4 <= len(password) <= 20) or not password.isalnum():
         raise HTTPException(status_code=400, detail="パスワードは4文字以上20文字以下の英数字にしてください")
         
    success = db_utils.register_user_request(
        nickname=nickname,
        password=password,
        ip=request.client.host,
        ua=req.ua,
        screen=req.screen
    )
    
    if not success:
         raise HTTPException(status_code=400, detail="登録に失敗しました (重複の可能性があります)")
    
    # Notify Admin
    add_notification("admin", f"新しいユーザー登録承認待ち: {nickname}", "info")
         
    return {"message": "登録リクエストを送信しました。承認をお待ちください。"}


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    try:
        # Check Auth & Role
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if not token or token not in sessions:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        if sessions[token].get('role') != 'admin':
            raise HTTPException(status_code=403, detail="Forbidden")

        # Get Disk Usage
        try:
            total, used, free = shutil.disk_usage(DOWNLOAD_DIR)
        except Exception as e:
            logging.error(f"Disk usage check failed: {e}")
            total, used, free = 0, 0, 0

        # Safe copy of active clients
        current_clients = active_clients.copy()
        
        # Get Logs & Bandwidth
        logs = db_utils.get_logs(limit=50)
        bandwidth = db_utils.get_bandwidth_stats()
        blocked_ips = db_utils.get_blocked_ips()
        clients = db_utils.get_clients()

        return {
            "active_clients": get_active_client_count(),
            "sessions": len(sessions),
            "clients_list": current_clients,
            "disk": {"total": total, "used": used, "free": free},
            "logs": logs,
            "bandwidth": bandwidth,
            "blocked_ips": blocked_ips,
            "clients": clients
        }
    except Exception as e:
        logging.error(f"Admin stats error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Admin User Management ---

@app.get("/api/admin/users")
async def get_users(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    return db_utils.get_all_users()

@app.post("/api/admin/users/{user_id}/approve")
async def approve_user_endpoint(user_id: int, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    db_utils.approve_user(user_id)
    return {"message": "User approved"}

@app.delete("/api/admin/users/{user_id}")
async def delete_user_endpoint(user_id: int, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    db_utils.delete_user(user_id)
    return {"message": "User deleted"}

@app.patch("/api/admin/users/{user_id}")
async def update_user_endpoint(user_id: int, req: UserUpdateRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    db_utils.update_user(user_id, password=req.password, role=req.role, username=req.username, nickname=req.nickname)
    return {"message": "User updated"}

@app.get("/api/admin/users/{user_id}/stats")
async def get_user_stats_endpoint(user_id: int, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    return db_utils.get_user_stats(user_id)

# --- Admin Log Management ---

@app.get("/api/admin/logs/files")
async def list_log_files(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    
    files = []
    base_name = 'server.log'
    if os.path.exists(base_name):
        files.append({"name": base_name, "mtime": os.path.getmtime(base_name), "size": os.path.getsize(base_name)})
    
    for i in range(1, 10):
        fname = f"{base_name}.{i}"
        if os.path.exists(fname):
             files.append({"name": fname, "mtime": os.path.getmtime(fname), "size": os.path.getsize(fname)})
    
    return sorted(files, key=lambda x: x['mtime'], reverse=True)

@app.get("/api/admin/logs/content")
async def get_log_content(file: str, request: Request, lines: int = 2000):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    
    # Basic security path traversal check
    if not file.startswith("server.log") or ".." in file or "/" in file or "\\" in file:
         raise HTTPException(status_code=400, detail="Invalid file")

    if not os.path.exists(file):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        # Read last N lines approximately
        # Check size
        size = os.path.getsize(file)
        if size > 1024 * 1024:
            # Read last 512KB
            seek_pos = max(0, size - (512 * 1024))
            async with aiofiles.open(file, mode='r', encoding='utf-8', errors='replace') as f:
                await f.seek(seek_pos)
                content = await f.read()
                # We might have started in middle of line
                if seek_pos > 0:
                    content = content.partition('\n')[2]
                return {"content": content}
        else:
            async with aiofiles.open(file, mode='r', encoding='utf-8', errors='replace') as f:
                content = await f.read()
                return {"content": content}
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/logs")
async def delete_logs(request: Request, file: str):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    
    # Security check
    if not file.startswith("server.log") or ".." in file or "/" in file or "\\" in file:
         raise HTTPException(status_code=400, detail="Invalid file")
         
    if os.path.exists(file):
        try:
            os.remove(file)
            return {"message": "Deleted"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/api/admin/logs/search")
async def search_logs(q: str, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403)
    
    results = []
    base_name = 'server.log'
    candidates = [base_name] + [f"{base_name}.{i}" for i in range(1, 10)]
    
    for fname in candidates:
        if os.path.exists(fname):
            try:
                async with aiofiles.open(fname, mode='r', encoding='utf-8', errors='replace') as f:
                    content = await f.read()
                    lines = content.splitlines()
                    for line in lines:
                        if q.lower() in line.lower():
                            results.append({"file": fname, "line": line.strip()})
            except:
                continue
    return results[:1000]

@app.post("/api/client/info")
async def client_info(request: Request, info: Dict = Body(...)):
    client_ip = request.client.host
    # Generate a simple fingerprint ID if not provided
    # In a real scenario, we'd use a library or more complex logic.
    # Here we trust the client to send some data, and we hash it + IP.
    
    # Create a unique ID based on the info provided
    fingerprint_str = f"{info.get('ua')}{info.get('screen')}{info.get('depth')}{client_ip}"
    client_id = hashlib.md5(fingerprint_str.encode()).hexdigest()[:12]
    
    db_utils.update_client_info(client_id, client_ip, info)
    return {"status": "ok", "client_id": client_id}

# --- File Manager API ---

@app.get("/api/admin/files")
async def list_files(request: Request, path: str = "", root: str = "app"):
    # Auth Check
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")
        
    # Determine Base Directory
    if root == "downloads":
        base_dir = DOWNLOAD_DIR
    elif root == "trash":
        base_dir = TRASH_DIR
    else:
        base_dir = execution_dir
    
    target_path = os.path.abspath(os.path.join(base_dir, path))
    if not target_path.startswith(os.path.abspath(base_dir)):
         raise HTTPException(status_code=403, detail="Access Denied")
         
    if not os.path.exists(target_path):
        # If root is trash/downloads and empty, it might not exist yet or be empty
        if root in ["downloads", "trash"] and path == "":
             return {"path": path, "items": []}
        raise HTTPException(status_code=404, detail="Path not found")
        
    if os.path.isfile(target_path):
        return FileResponse(target_path)
        
    items = []
    try:
        with os.scandir(target_path) as it:
            for entry in it:
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if not entry.is_dir() else 0,
                    "mtime": entry.stat().st_mtime
                })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"path": path, "items": items}

@app.delete("/api/admin/files")
async def delete_file_admin(path: str, request: Request, root: str = "app"):
    # Auth Check
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")

    # Determine Base Directory
    if root == "downloads":
        base_dir = DOWNLOAD_DIR
    elif root == "trash":
        base_dir = TRASH_DIR
    else:
        base_dir = execution_dir

    target_path = os.path.abspath(os.path.join(base_dir, path))
    if not target_path.startswith(os.path.abspath(base_dir)):
         raise HTTPException(status_code=403, detail="Access Denied")
         
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Not found")
        
    try:
        # If deleting from Downloads, move to Trash instead?
        # User asked to "manage" trash.
        if root == "downloads":
            # Move to Trash
            trash_path = os.path.join(TRASH_DIR, os.path.basename(target_path))
            # Handle collision
            if os.path.exists(trash_path):
                base, ext = os.path.splitext(trash_path)
                trash_path = f"{base}_{int(time.time())}{ext}"
            shutil.move(target_path, trash_path)
        else:
            # Permanent delete (from Trash or App)
            if os.path.isdir(target_path):
                shutil.rmtree(target_path)
            else:
                os.remove(target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "deleted"}

class RenameRequest(BaseModel):
    path: str
    new_name: str
    root: str = "app"

@app.post("/api/admin/files/rename")
async def rename_file_admin(req: RenameRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")

    # Determine Base Directory
    if req.root == "downloads":
        base_dir = DOWNLOAD_DIR
    elif req.root == "trash":
        base_dir = TRASH_DIR
    else:
        base_dir = execution_dir
        
    old_path = os.path.abspath(os.path.join(base_dir, req.path))
    new_path = os.path.abspath(os.path.join(os.path.dirname(old_path), req.new_name))
    
    if not old_path.startswith(os.path.abspath(base_dir)) or not new_path.startswith(os.path.abspath(base_dir)):
         raise HTTPException(status_code=403, detail="Access Denied")
         
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        os.rename(old_path, new_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"message": "Renamed"}

@app.post("/api/admin/files/upload")
async def upload_file_admin(request: Request, file: UploadFile = File(...), path: str = "", root: str = "app"):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")

    # Determine Base Directory
    if root == "downloads":
        base_dir = DOWNLOAD_DIR
    elif root == "trash":
        base_dir = TRASH_DIR
    else:
        base_dir = execution_dir
        
    target_dir = os.path.abspath(os.path.join(base_dir, path))
    if not target_dir.startswith(os.path.abspath(base_dir)):
         raise HTTPException(status_code=403, detail="Access Denied")
    
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
        
    file_path = os.path.join(target_dir, file.filename)
    try:
        async with aiofiles.open(file_path, 'wb') as f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                await f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"message": "Uploaded"}

class FileContentRequest(BaseModel):
    path: str
    content: str
    root: str = "app"

@app.post("/api/admin/files/content")
async def save_file_content(req: FileContentRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")

    # Determine Base Directory
    if req.root == "downloads":
        base_dir = DOWNLOAD_DIR
    elif req.root == "trash":
        base_dir = TRASH_DIR
    else:
        base_dir = execution_dir
        
    target_path = os.path.abspath(os.path.join(base_dir, req.path))
    if not target_path.startswith(os.path.abspath(base_dir)):
         raise HTTPException(status_code=403, detail="Access Denied")
         
    try:
        async with aiofiles.open(target_path, 'w', encoding='utf-8') as f:
            await f.write(req.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"message": "Saved"}

class BlockIPRequest(BaseModel):
    ip: str
    reason: str = ""

@app.post("/api/admin/block_ip")
async def block_ip_endpoint(req: BlockIPRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db_utils.block_ip(req.ip, req.reason)
    db_utils.log_event(request.client.host, "BLOCK_IP", f"Blocked {req.ip}: {req.reason}")
    return {"message": f"Blocked {req.ip}"}

@app.post("/api/admin/unblock_ip")
async def unblock_ip_endpoint(req: BlockIPRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")
    
    db_utils.unblock_ip(req.ip)
    db_utils.log_event(request.client.host, "UNBLOCK_IP", f"Unblocked {req.ip}")
    return {"message": f"Unblocked {req.ip}"}

# --- Proxy Endpoints ---

class ProxyEncryptRequest(BaseModel):
    url: str

@app.post("/api/proxy/encrypt")
async def proxy_encrypt(req: ProxyEncryptRequest):
    payload = proxy_service.encrypt_payload(req.url)
    return {"payload": payload}

@app.get("/api/proxy/resource")
async def proxy_resource(payload: str, request: Request):
    """GET endpoint for proxied resources (images, scripts, css)"""
    try:
        client_ip = request.client.host
        if db_utils.is_ip_blocked(client_ip):
             return Response(content="Access Denied", status_code=403)

        # Determine Speed Limit
        token = request.cookies.get(AUTH_COOKIE_NAME)
        role = "guest"
        if token and token in sessions:
            role = sessions[token].get('role', 'user')
            
        limit_mb = LIMITS.get(role, {}).get('speed_limit', 0)
        limit_bps = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None

        data = proxy_service.decrypt_payload(payload)
        url = data['url']
        
        resp = await proxy_service.proxy_request(url, client_ip)
        
        # Filter security headers
        resp_headers = getattr(resp, 'headers', {}) or {}
        skip_headers = {
            'content-security-policy', 'content-security-policy-report-only',
            'x-content-security-policy', 'x-webkit-csp', 'x-frame-options',
            'x-xss-protection', 'permissions-policy', 'cross-origin-embedder-policy',
            'cross-origin-opener-policy', 'cross-origin-resource-policy'
        }
        clean_headers = {"Content-Disposition": resp_headers.get("Content-Disposition", "")}
        
        # Stream response
        return StreamingResponse(
            proxy_service.stream_response(resp, client_ip, limit_bps),
            media_type=resp_headers.get("content-type", "application/octet-stream"),
            headers=clean_headers
        )
    except Exception as e:
        return Response(status_code=404)

@app.post("/proxy")
async def proxy_handler(payload: str = Form(...), request: Request = None):
    try:
        # Validate Request
        if request is None:
             return Response(content="Proxy Internal Error: Request object missing.", status_code=500)

        client_ip = getattr(request.client, 'host', "unknown") if request.client else "unknown"
        
        # Check Blocked IP
        if db_utils.is_ip_blocked(client_ip):
             return Response(content="Access Denied: Your IP is blocked.", status_code=403)

        # Rate Limit Check
        cookies = getattr(request, 'cookies', {}) or {}
        token = cookies.get(AUTH_COOKIE_NAME)
        username = None
        role = "guest"
        if token and token in sessions:
            session = sessions[token]
            if session: # Ensure session is not None
                username = session.get('username')
                role = session.get('role', 'user')
            
        if username:
            if not check_rate_limit(username, role, 'proxy'):
                return Response(content="API Limit Exceeded: Proxy quota reached for this hour.", status_code=429)
            add_rate_limit_usage(username, 'proxy')

        # Safely get limits
        role_limits = LIMITS.get(role, {})
        limit_mb = 0
        if role_limits:
            limit_mb = role_limits.get('speed_limit', 0)
        
        limit_bps = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None

        data = proxy_service.decrypt_payload(payload)
        url = data.get('url')
        if not url:
             raise ValueError("No URL in payload")
        
        # Execute Proxy Request
        resp = await proxy_service.proxy_request(url, client_ip)
        
        if not resp:
             raise ValueError("Proxy request returned no response")

        # Rewrite HTML if content type is html
        # Safely access headers
        headers = getattr(resp, 'headers', {}) or {}
        content_type = headers.get("content-type", "")
        
        # Filter out security headers from proxied response
        filtered_headers = {}
        skip_headers = {
            'content-security-policy', 'content-security-policy-report-only',
            'x-content-security-policy', 'x-webkit-csp', 'x-frame-options',
            'x-xss-protection', 'permissions-policy', 'cross-origin-embedder-policy',
            'cross-origin-opener-policy', 'cross-origin-resource-policy',
            'strict-transport-security', 'referrer-policy'
        }
        for k, v in headers.items():
            if k.lower() not in skip_headers:
                filtered_headers[k] = v
        
        if "text/html" in content_type:
            content = await resp.aread()
            # Log bandwidth for non-streamed content
            db_utils.log_bandwidth(client_ip, len(content), 0, "proxy")
            
            rewritten = proxy_service.rewrite_html(content, url)
            return Response(content=rewritten, media_type="text/html; charset=utf-8")
        else:
            # Stream other content with limit
            # Prepare clean headers for streaming response
            stream_headers = {"Content-Disposition": filtered_headers.get("Content-Disposition", "")}
            return StreamingResponse(
                proxy_service.stream_response(resp, client_ip, limit_bps),
                media_type=content_type,
                headers=stream_headers
            )

    except HTTPException as he:
        # Return a friendly HTML error page instead of plain text
        error_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Proxy Error</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
               color: #fff; display: flex; justify-content: center; align-items: center; 
               min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }}
        .error-box {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); 
                     border-radius: 16px; padding: 40px; max-width: 500px; text-align: center;
                     box-shadow: 0 8px 32px rgba(0,0,0,0.3); }}
        h1 {{ color: #ff6b6b; margin-bottom: 20px; }}
        p {{ color: #ddd; line-height: 1.6; margin-bottom: 25px; }}
        .status {{ font-size: 48px; font-weight: bold; color: #ff6b6b; margin-bottom: 10px; }}
        .btn {{ background: #4361ee; color: #fff; padding: 12px 30px; border-radius: 8px; 
               text-decoration: none; display: inline-block; margin: 5px; transition: all 0.3s; }}
        .btn:hover {{ background: #3a56d4; transform: translateY(-2px); }}
        .btn-secondary {{ background: rgba(255,255,255,0.2); }}
        code {{ background: rgba(0,0,0,0.3); padding: 2px 8px; border-radius: 4px; font-size: 0.9em; }}
    </style>
</head>
<body>
    <div class="error-box">
        <div class="status">{he.status_code}</div>
        <h1>Proxy Error</h1>
        <p>{he.detail}</p>
        <a href="javascript:history.back()" class="btn btn-secondary">← Go Back</a>
        <a href="javascript:location.reload()" class="btn">Try Again</a>
    </div>
</body>
</html>"""
        return Response(content=error_html, status_code=he.status_code, media_type="text/html; charset=utf-8")
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logging.error(f"Proxy failed: {e}\n{error_details}")
        # Return a friendly HTML error page
        error_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Proxy Error</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); 
               color: #fff; display: flex; justify-content: center; align-items: center; 
               min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }}
        .error-box {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); 
                     border-radius: 16px; padding: 40px; max-width: 500px; text-align: center;
                     box-shadow: 0 8px 32px rgba(0,0,0,0.3); }}
        h1 {{ color: #ff6b6b; margin-bottom: 20px; }}
        p {{ color: #ddd; line-height: 1.6; margin-bottom: 25px; }}
        .status {{ font-size: 48px; font-weight: bold; color: #ff6b6b; margin-bottom: 10px; }}
        .btn {{ background: #4361ee; color: #fff; padding: 12px 30px; border-radius: 8px; 
               text-decoration: none; display: inline-block; margin: 5px; transition: all 0.3s; }}
        .btn:hover {{ background: #3a56d4; transform: translateY(-2px); }}
        .btn-secondary {{ background: rgba(255,255,255,0.2); }}
        .details {{ background: rgba(0,0,0,0.3); padding: 15px; border-radius: 8px; 
                   margin-top: 20px; text-align: left; font-size: 0.85em; word-break: break-all; }}
    </style>
</head>
<body>
    <div class="error-box">
        <div class="status">500</div>
        <h1>Proxy Internal Error</h1>
        <p>An unexpected error occurred while processing your request.</p>
        <a href="javascript:history.back()" class="btn btn-secondary">← Go Back</a>
        <a href="javascript:location.reload()" class="btn">Try Again</a>
        <div class="details"><strong>Error:</strong> {str(e)}</div>
    </div>
</body>
</html>"""
        return Response(content=error_html, status_code=500, media_type="text/html; charset=utf-8")

@app.get("/proxy")
async def proxy_get_handler(request: Request):
    """Handle GET requests to /proxy gracefully - these are usually errors from JS redirects"""
    # If there are query params, it's likely a failed redirect from proxied content
    if request.query_params:
        # Return a script that tries to handle navigation issues gracefully
        return Response(
            content="""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Redirecting...</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #0f0f23; color: #ccc; display: flex; justify-content: center;
               align-items: center; min-height: 100vh; margin: 0; }
        .box { text-align: center; }
        .spinner { width: 40px; height: 40px; border: 3px solid #333; border-top: 3px solid #4361ee;
                   border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 20px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
    <script>
        // The proxied site tried to navigate - handle gracefully
        setTimeout(function() {
            // Try to go back, or reload parent frame, or just close
            if (window.history.length > 1) {
                window.history.back();
            } else if (window.parent && window.parent !== window) {
                // In iframe - try to reload parent
                try { window.parent.location.reload(); } catch(e) {}
            } else {
                // Nothing worked - just hide the spinner and show done
                document.querySelector('.spinner').style.display = 'none';
                document.querySelector('.box').innerHTML = '<p>Navigation completed</p><a href="/" style="color:#4361ee;">Go to Home</a>';
            }
        }, 500);
    </script>
</head>
<body>
    <div class="box">
        <div class="spinner"></div>
        <p>Processing...</p>
    </div>
</body>
</html>""",
            media_type="text/html; charset=utf-8"
        )
    return RedirectResponse(url="/")

# --- System Endpoints ---

@app.get("/debug/info")
async def debug_info():
    """Diagnostic endpoint to check server state"""
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        try:
            files = os.listdir(DOWNLOAD_DIR)
        except Exception as e:
            files = [f"Error listing files: {str(e)}"]
    return {
        "download_dir": DOWNLOAD_DIR,
        "dir_exists": os.path.exists(DOWNLOAD_DIR),
        "files": files,
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "ffmpeg_found": ffmpeg_found
    }

@app.get("/api/download/{filename}")
async def download_file(filename: str, request: Request):
    """Direct download endpoint with Range support"""
    # Security check
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    
    # Log Download Event
    client_ip = request.client.host
    file_size = os.path.getsize(file_path)
    db_utils.log_event(client_ip, "DOWNLOAD", f"File: {filename}")
    db_utils.log_bandwidth(client_ip, 0, file_size, "download")

    # Use FileResponse for proper Range support (seeking)
    return FileResponse(
        file_path, 
        media_type="application/octet-stream", 
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/info")
async def get_info(url: str):
    """Get video info (no download)"""
    ydl_opts = {'quiet': True, 'no_warnings': True}
    
    cookie_file = os.path.join(execution_dir, "cookies.txt")
    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get('title'),
                "duration": info.get('duration'),
                "uploader": info.get('uploader'),
                "view_count": info.get('view_count'),
                "url": info.get('url')
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/user/password")
async def change_password(req: ChangePasswordRequest, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    username = sessions[token].get('username')
    role = sessions[token].get('role')
    
    # Authenticate current password (unless admin changing own?)
    # Users in DB have ID. users in USERS dict (fallback) don't.
    # We only support changing DB users for now.
    
    # Verify current password
    auth_role = db_utils.authenticate_user(username, req.current_password)
    
    if not auth_role:
         raise HTTPException(status_code=403, detail="Invalid current password")

    if len(req.new_password) < 4:
         raise HTTPException(status_code=400, detail="Password too short")
         
    # Update DB
    db_utils.update_user_password(username, req.new_password)
    return {"message": "Password updated"}

@app.get("/system/info")
async def system_info(request: Request):
    """Get system status and load info"""
    active_jobs = len([j for j in jobs.values() if j.status in [JobStatus.QUEUED, JobStatus.DOWNLOADING]])
    
    # Determine role
    role = "guest"
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token and token in sessions:
        role = sessions[token].get("role", "user")

    resp = {
        "hostname": socket.gethostname(),
        "active_jobs": active_jobs,
        "active_clients": get_active_client_count(),
        "platform": sys.platform,
        "version": app.version,
        "role": role
    }
    
    if role == 'admin':
        resp['pending_users'] = db_utils.get_pending_users_count()
        
    return resp

@app.get("/api/search")
async def search_youtube_endpoint(q: str):
    """Search YouTube for videos"""
    try:
        def search():
            ydl_opts = {
                'quiet': True,
                'extract_flat': 'in_playlist', # Better for search results
                'default_search': 'ytsearch10',
                'noplaylist': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # If q is not a url, ytsearch10: is prefixed by default_search
                # We need to handle URL vs Search Query manually because extract_flat for URL returns different structure
                
                res = ydl.extract_info(q, download=False)
                if 'entries' in res:
                    return res['entries']
                # If it's a direct match or single video from search logic
                return [res] if res else []
        
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(executor, search)
        return results
    except Exception as e:
        logging.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token and token in sessions:
        del sessions[token]
    
    db_utils.log_event(request.client.host, "LOGOUT", "")
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"message": "Logged out"}

@app.get("/api/notifications")
async def get_notifications(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in sessions:
         return []
    
    username = sessions[token].get('username')
    role = sessions[token].get('role')
    
    notifs = []
    
    # 1. User specific notifications (from memory)
    if username in user_notifications:
        notifs.extend(user_notifications[username])
        # Clear fetched
        user_notifications[username] = []
        
    # 2. Role specific checks
    if role == 'admin':
        pending = db_utils.get_pending_users_count()
        if pending > 0:
            notifs.append({
                "id": "pending_users",
                "message": f"承認待ちユーザーが {pending} 人います",
                "type": "warning",
                "timestamp": time.time()
            })
            
    return notifs

@app.get("/api/preview/{filename}")
async def preview_video(filename: str, request: Request):
    """
    Transcoded preview for heavy videos. 
    Output: Low bitrate MP4 for smooth playback.
    """
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in sessions:
         raise HTTPException(status_code=401)
    
    # Security Check
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400)
         
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404)
        
    # ffmpeg command to transcode
    cmd = [
        "ffmpeg",
        "-i", file_path,
        "-vf", "scale=-2:480", # Downscale to 480p
        "-c:v", "libx264",
        "-b:v", "500k",        # 500kbps video
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "64k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "-"
    ]
    
    # Async generator
    async def iter_ffmpeg():
        try:
            # Hide console window on Windows
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                startupinfo=startupinfo
            )
            
            while True:
                chunk = await proc.stdout.read(1024 * 64)
                if not chunk:
                    break
                yield chunk
            await proc.wait()
        except Exception as e:
            logging.error(f"FFmpeg Preview Error: {e}")

    return StreamingResponse(iter_ffmpeg(), media_type="video/mp4")

@app.post("/system/cookies")
async def upload_cookies(request: Request, file: UploadFile = File(...)):
    """Upload cookies.txt file"""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or sessions.get(token, {}).get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        file_location = os.path.join(execution_dir, "cookies.txt")
        with open(file_location, "wb+") as file_object:
            file_object.write(await file.read())
        return {"message": f"Cookies saved successfully."}
    except Exception as e:
        logging.error(f"Failed to save cookies: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save cookies: {str(e)}")

import subprocess

@app.post("/system/update")
async def update_system():
    """
    Triggers the update process.
    This will pull the latest code from git, rebuild, and restart the server.
    """
    try:
        # Run update_app.ps1 in a separate process
        # We use Popen to let it run independently and return response immediately
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", "update_app.ps1"],
            cwd=os.getcwd(),
            creationflags=subprocess.CREATE_NEW_CONSOLE # Windows only: Create new window/process group
        )
        return {"message": "Update started. Server will restart in a few minutes."}
    except Exception as e:
        logging.error(f"Update failed to start: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start update: {str(e)}")

@app.get("/beta/{version}")
async def update_beta(version: str):
    """
    Triggers the update process to a specific beta version.
    """
    try:
        # Run update_app.ps1 with -Beta and -Version
        subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", "update_app.ps1", "-Beta", "-Version", version],
            cwd=os.getcwd(),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        return {"message": f"Beta update to {version} started. Server will restart in a few minutes."}
    except Exception as e:
        logging.error(f"Beta update failed to start: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start beta update: {str(e)}")


# ============================================================
# 早押しクイズ機能 (みんはや風)
# ============================================================

# --- クイズデータ管理 ---
HAYAOSHI_QUESTIONS_PATH = os.path.join(execution_dir, "data", "quiz", "hayaoshi-questions.json")

def load_hayaoshi_questions():
    """早押しクイズの問題を読み込む"""
    try:
        if os.path.exists(HAYAOSHI_QUESTIONS_PATH):
            with open(HAYAOSHI_QUESTIONS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load hayaoshi questions: {e}")
    return []

# --- プレイヤークラス ---
class QuizPlayer:
    def __init__(self, ws: WebSocket, player_id: str, name: str):
        self.ws = ws
        self.player_id = player_id
        self.name = name
        self.score = 0
        self.rating = 1500  # ELOレーティング初期値
        self.is_ready = False
        self.can_answer = True  # この問題で回答権があるか
        self.ping_ms = 0  # Ping値（遅延補正用）
        
    async def send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            pass

# --- ゲームルームクラス ---
class QuizRoom:
    def __init__(self, room_id: str, host_id: str, is_ranked: bool = False):
        self.room_id = room_id
        self.host_id = host_id
        self.is_ranked = is_ranked
        self.players: Dict[str, QuizPlayer] = {}
        self.state = "waiting"  # waiting, playing, finished
        self.questions: List[dict] = []
        self.current_question_index = 0
        self.current_question_text = ""
        self.revealed_chars = 0
        self.answering_player_id: Optional[str] = None
        self.answer_start_time: Optional[float] = None
        self.question_start_time: Optional[float] = None
        self.buzz_times: Dict[str, float] = {}  # プレイヤーごとのボタン押下時刻
        self.max_questions = 10
        self.char_reveal_task: Optional[asyncio.Task] = None
        self.created_at = time.time()
        
    async def broadcast(self, data: dict, exclude: Optional[str] = None):
        """全プレイヤーにメッセージを送信"""
        for pid, player in self.players.items():
            if exclude and pid == exclude:
                continue
            await player.send(data)
    
    def get_player_list(self):
        """プレイヤー一覧を取得"""
        return [
            {
                "id": p.player_id,
                "name": p.name,
                "score": p.score,
                "rating": p.rating,
                "isReady": p.is_ready,
                "isHost": p.player_id == self.host_id
            }
            for p in self.players.values()
        ]
    
    def all_ready(self):
        """全員準備完了か確認"""
        if len(self.players) < 2:
            return False
        return all(p.is_ready for p in self.players.values())
    
    async def start_game(self):
        """ゲーム開始"""
        self.state = "playing"
        all_questions = load_hayaoshi_questions()
        if len(all_questions) < self.max_questions:
            self.questions = all_questions
        else:
            self.questions = random.sample(all_questions, self.max_questions)
        self.current_question_index = 0
        
        await self.broadcast({
            "type": "game_start",
            "totalQuestions": len(self.questions)
        })
        
        await asyncio.sleep(2)  # カウントダウン
        await self.next_question()
    
    async def next_question(self):
        """次の問題へ"""
        if self.current_question_index >= len(self.questions):
            await self.end_game()
            return
            
        q = self.questions[self.current_question_index]
        self.current_question_text = q["question"]
        self.revealed_chars = 0
        self.answering_player_id = None
        self.buzz_times = {}
        self.question_start_time = time.time()
        
        # 全プレイヤーの回答権をリセット
        for p in self.players.values():
            p.can_answer = True
        
        await self.broadcast({
            "type": "question_start",
            "questionNumber": self.current_question_index + 1,
            "totalQuestions": len(self.questions),
            "category": q.get("category", "")
        })
        
        # 文字を1文字ずつ送信開始
        self.char_reveal_task = asyncio.create_task(self.reveal_chars())
    
    async def reveal_chars(self):
        """問題文を1文字ずつ公開"""
        try:
            for i, char in enumerate(self.current_question_text):
                if self.answering_player_id:
                    # 誰かが回答中なら停止
                    return
                
                self.revealed_chars = i + 1
                await self.broadcast({
                    "type": "char",
                    "char": char,
                    "index": i
                })
                await asyncio.sleep(0.08)  # 1文字0.08秒
            
            # 全文表示後、5秒待ってスルー判定
            await asyncio.sleep(5)
            if not self.answering_player_id:
                await self.handle_timeout()
                
        except asyncio.CancelledError:
            pass
    
    async def handle_buzz(self, player_id: str, client_timestamp: float):
        """早押しボタン処理"""
        if self.answering_player_id:
            return  # 既に誰か回答中
            
        player = self.players.get(player_id)
        if not player or not player.can_answer:
            return
            
        # サーバー時刻を記録（Ping補正込み）
        server_time = time.time()
        adjusted_time = server_time - (player.ping_ms / 1000 / 2)  # RTT/2を引く
        self.buzz_times[player_id] = adjusted_time
        
        # 少し待って最速を判定（複数人が同時に押した場合の対策）
        await asyncio.sleep(0.05)
        
        if self.answering_player_id:
            return  # 既に判定済み
            
        # 最速のプレイヤーを選出
        if self.buzz_times:
            fastest_id = min(self.buzz_times, key=self.buzz_times.get)
            self.answering_player_id = fastest_id
            self.answer_start_time = time.time()
            
            # 文字表示を停止
            if self.char_reveal_task:
                self.char_reveal_task.cancel()
            
            await self.broadcast({
                "type": "buzz_accepted",
                "playerId": fastest_id,
                "playerName": self.players[fastest_id].name,
                "revealedText": self.current_question_text[:self.revealed_chars]
            })
            
            # 回答用の文字パネルを生成して送信
            q = self.questions[self.current_question_index]
            panels = self.generate_char_panels(q["reading"])
            await self.players[fastest_id].send({
                "type": "show_panels",
                "panels": panels,
                "answerLength": len(q["reading"])
            })
    
    def generate_char_panels(self, reading: str) -> List[str]:
        """文字パネルを生成（正解文字 + ダミー文字）"""
        # ひらがな一覧
        hiragana = list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん")
        hiragana += list("がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ")
        hiragana += list("ぁぃぅぇぉっゃゅょー")
        
        # 正解の文字
        answer_chars = list(reading)
        
        # ダミー文字を追加（合計12〜16文字）
        target_count = random.randint(12, 16)
        dummy_count = max(0, target_count - len(answer_chars))
        
        # 正解に含まれない文字からダミーを選ぶ
        available_dummies = [c for c in hiragana if c not in answer_chars]
        dummies = random.sample(available_dummies, min(dummy_count, len(available_dummies)))
        
        # 全パネルをシャッフル
        all_panels = answer_chars + dummies
        random.shuffle(all_panels)
        
        return all_panels
    
    async def handle_answer(self, player_id: str, answer: str):
        """回答を処理"""
        if self.answering_player_id != player_id:
            return
            
        q = self.questions[self.current_question_index]
        correct_reading = q["reading"]
        
        # 正誤判定（読み仮名で比較）
        # カタカナ→ひらがな変換
        answer_normalized = answer.translate(str.maketrans(
            'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポァィゥェォッャュョー',
            'あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽぁぃぅぇぉっゃゅょー'
        ))
        
        is_correct = answer_normalized == correct_reading
        
        player = self.players[player_id]
        
        if is_correct:
            # 正解
            points = q.get("points", 10)
            player.score += points
            
            await self.broadcast({
                "type": "answer_result",
                "correct": True,
                "playerId": player_id,
                "playerName": player.name,
                "answer": q["answer"],
                "reading": q["reading"],
                "points": points,
                "scores": {p.player_id: p.score for p in self.players.values()}
            })
            
            # 次の問題へ
            await asyncio.sleep(2)
            self.current_question_index += 1
            self.answering_player_id = None
            await self.next_question()
            
        else:
            # 不正解
            player.can_answer = False  # この問題の回答権を剥奪
            player.score = max(0, player.score - 5)  # 減点
            
            await self.broadcast({
                "type": "answer_result",
                "correct": False,
                "playerId": player_id,
                "playerName": player.name,
                "wrongAnswer": answer,
                "scores": {p.player_id: p.score for p in self.players.values()}
            })
            
            self.answering_player_id = None
            self.buzz_times = {}
            
            # 回答権のあるプレイヤーがいるか確認
            remaining = [p for p in self.players.values() if p.can_answer]
            if remaining:
                # 問題文の表示を再開
                await asyncio.sleep(1)
                self.char_reveal_task = asyncio.create_task(self.continue_reveal())
            else:
                # 全員不正解 → スルー
                await self.handle_timeout()
    
    async def continue_reveal(self):
        """問題文の残りを表示"""
        try:
            for i in range(self.revealed_chars, len(self.current_question_text)):
                if self.answering_player_id:
                    return
                    
                char = self.current_question_text[i]
                self.revealed_chars = i + 1
                await self.broadcast({
                    "type": "char",
                    "char": char,
                    "index": i
                })
                await asyncio.sleep(0.08)
            
            await asyncio.sleep(5)
            if not self.answering_player_id:
                await self.handle_timeout()
                
        except asyncio.CancelledError:
            pass
    
    async def handle_timeout(self):
        """タイムアウト（スルー）処理"""
        q = self.questions[self.current_question_index]
        
        await self.broadcast({
            "type": "timeout",
            "answer": q["answer"],
            "reading": q["reading"],
            "fullQuestion": q["question"]
        })
        
        await asyncio.sleep(3)
        self.current_question_index += 1
        self.answering_player_id = None
        await self.next_question()
    
    async def end_game(self):
        """ゲーム終了"""
        self.state = "finished"
        
        # 順位を計算
        sorted_players = sorted(self.players.values(), key=lambda p: p.score, reverse=True)
        rankings = [
            {"rank": i + 1, "id": p.player_id, "name": p.name, "score": p.score}
            for i, p in enumerate(sorted_players)
        ]
        
        # ELOレーティング更新（ランクマッチの場合）
        if self.is_ranked and len(sorted_players) >= 2:
            winner = sorted_players[0]
            loser = sorted_players[1]
            # 簡易ELO計算
            k = 32
            expected_winner = 1 / (1 + 10 ** ((loser.rating - winner.rating) / 400))
            winner.rating += int(k * (1 - expected_winner))
            loser.rating += int(k * (0 - (1 - expected_winner)))
        
        await self.broadcast({
            "type": "game_end",
            "rankings": rankings
        })


# --- ルーム管理 ---
quiz_rooms: Dict[str, QuizRoom] = {}
matchmaking_queue: List[QuizPlayer] = []


@app.get("/api/quiz/rooms")
async def get_quiz_rooms():
    """公開ルーム一覧を取得"""
    rooms = []
    current_time = time.time()
    for room_id, room in list(quiz_rooms.items()):
        # Clean up empty old rooms (older than 10 mins)
        if not room.players and current_time - room.created_at > 600:
            del quiz_rooms[room_id]
            continue

        if room.state == "waiting":
            host_name = "Unknown"
            if room.host_id in room.players:
                host_name = room.players[room.host_id].name
                
            rooms.append({
                "id": room_id,
                "hostName": host_name,
                "playerCount": len(room.players),
                "isRanked": room.is_ranked,
                "createdAt": room.created_at
            })
    return {"rooms": rooms}


@app.post("/api/quiz/rooms")
async def create_quiz_room(request: Request):
    """新しいルームを作成"""
    data = await request.json()
    room_id = secrets.token_urlsafe(6)
    host_id = data.get("hostId", secrets.token_urlsafe(8))
    is_ranked = data.get("isRanked", False)
    
    room = QuizRoom(room_id, host_id, is_ranked)
    quiz_rooms[room_id] = room
    
    return {"roomId": room_id, "hostId": host_id}


@app.get("/api/quiz/questions")
async def get_quiz_questions_list():
    """早押しクイズの問題一覧を取得（管理用）"""
    questions = load_hayaoshi_questions()
    return {"questions": questions, "count": len(questions)}


@app.post("/api/quiz/questions")
async def add_quiz_question(request: Request):
    """新しい問題を追加"""
    data = await request.json()
    
    # バリデーション
    required = ["question", "answer", "reading"]
    for field in required:
        if field not in data:
            raise HTTPException(status_code=400, detail=f"Missing field: {field}")
    
    questions = load_hayaoshi_questions()
    
    # 新しいIDを生成
    new_id = f"h{len(questions) + 1:03d}"
    
    new_question = {
        "id": new_id,
        "category": data.get("category", "ユーザー投稿"),
        "difficulty": data.get("difficulty", "Normal"),
        "question": data["question"],
        "answer": data["answer"],
        "reading": data["reading"],
        "points": data.get("points", 10)
    }
    
    questions.append(new_question)
    
    # 保存
    os.makedirs(os.path.dirname(HAYAOSHI_QUESTIONS_PATH), exist_ok=True)
    with open(HAYAOSHI_QUESTIONS_PATH, 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    
    return {"success": True, "question": new_question}


@app.post("/api/quiz/questions/import")
async def import_quiz_questions(file: UploadFile = File(...)):
    """CSVファイルから問題をインポート"""
    import csv
    from io import StringIO
    
    content = await file.read()
    text = content.decode('utf-8-sig')  # BOM対応
    
    reader = csv.DictReader(StringIO(text))
    
    questions = load_hayaoshi_questions()
    imported_count = 0
    
    for row in reader:
        if "question" in row and "answer" in row and "reading" in row:
            new_id = f"h{len(questions) + 1:03d}"
            new_question = {
                "id": new_id,
                "category": row.get("category", "インポート"),
                "difficulty": row.get("difficulty", "Normal"),
                "question": row["question"],
                "answer": row["answer"],
                "reading": row["reading"],
                "points": int(row.get("points", 10))
            }
            questions.append(new_question)
            imported_count += 1
    
    # 保存
    os.makedirs(os.path.dirname(HAYAOSHI_QUESTIONS_PATH), exist_ok=True)
    with open(HAYAOSHI_QUESTIONS_PATH, 'w', encoding='utf-8') as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    
    return {"success": True, "importedCount": imported_count}


@app.websocket("/ws/quiz/{room_id}")
async def quiz_websocket(websocket: WebSocket, room_id: str):
    """クイズルームのWebSocket接続"""
    await websocket.accept()
    
    player: Optional[QuizPlayer] = None
    room: Optional[QuizRoom] = None
    
    try:
        # 初期接続メッセージを待つ
        init_data = await websocket.receive_json()
        
        if init_data.get("type") != "join":
            await websocket.close(code=4000, reason="Invalid init message")
            return
        
        player_id = init_data.get("playerId", secrets.token_urlsafe(8))
        player_name = init_data.get("name", f"ゲスト{random.randint(1000, 9999)}")
        
        # ルームを取得または作成
        if room_id == "matchmaking":
            # ランダムマッチ
            player = QuizPlayer(websocket, player_id, player_name)
            matchmaking_queue.append(player)
            
            await player.send({"type": "matchmaking_start"})
            
            # マッチング待機
            while len(matchmaking_queue) < 2:
                await asyncio.sleep(0.5)
                # 接続確認
                try:
                    await websocket.send_json({"type": "ping"})
                except:
                    matchmaking_queue.remove(player)
                    return
            
            # 2人揃ったらルーム作成
            if player in matchmaking_queue:
                if matchmaking_queue[0] == player:
                    # 最初のプレイヤーがホストとしてルーム作成
                    new_room_id = secrets.token_urlsafe(6)
                    room = QuizRoom(new_room_id, player_id, is_ranked=True)
                    quiz_rooms[new_room_id] = room
                    
                    # 2人目を取得
                    p2 = matchmaking_queue[1]
                    matchmaking_queue.clear()
                    
                    # 両方をルームに追加
                    room.players[player_id] = player
                    room.players[p2.player_id] = p2
                    
                    await player.send({"type": "matched", "roomId": new_room_id})
                    await p2.send({"type": "matched", "roomId": new_room_id})
                    
                    # ロビー情報を送信
                    await room.broadcast({
                        "type": "lobby_update",
                        "players": room.get_player_list()
                    })
                else:
                    # 2人目はホストのルームに参加するのを待つ
                    await asyncio.sleep(0.5)
                    return
        else:
            # 通常のルーム参加
            room = quiz_rooms.get(room_id)
            if not room:
                # 新規ルーム作成
                room = QuizRoom(room_id, player_id, is_ranked=False)
                quiz_rooms[room_id] = room
            
            player = QuizPlayer(websocket, player_id, player_name)
            room.players[player_id] = player
            
            await player.send({
                "type": "joined",
                "roomId": room_id,
                "playerId": player_id,
                "isHost": player_id == room.host_id
            })
            
            await room.broadcast({
                "type": "lobby_update",
                "players": room.get_player_list()
            })
        
        # メッセージループ
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "ping":
                # Ping応答 + RTT計測
                await player.send({"type": "pong", "serverTime": time.time()})
                
            elif msg_type == "pong":
                # クライアントからのPong（RTT計測用）
                client_time = data.get("clientTime", 0)
                if client_time:
                    player.ping_ms = (time.time() - client_time) * 1000
                    
            elif msg_type == "ready":
                # 準備完了
                player.is_ready = data.get("ready", True)
                await room.broadcast({
                    "type": "lobby_update",
                    "players": room.get_player_list()
                })
                
                # 全員準備完了ならゲーム開始
                if room.all_ready():
                    await room.start_game()
                    
            elif msg_type == "buzz":
                # 早押しボタン
                client_timestamp = data.get("timestamp", time.time())
                await room.handle_buzz(player_id, client_timestamp)
                
            elif msg_type == "answer":
                # 回答送信
                answer = data.get("answer", "")
                await room.handle_answer(player_id, answer)
                
            elif msg_type == "chat":
                # チャットメッセージ
                await room.broadcast({
                    "type": "chat",
                    "playerId": player_id,
                    "playerName": player.name,
                    "message": data.get("message", "")
                })
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Quiz WebSocket error: {e}")
    finally:
        # クリーンアップ
        if player and player in matchmaking_queue:
            matchmaking_queue.remove(player)
        if room and player:
            room.players.pop(player.player_id, None)
            if len(room.players) == 0:
                quiz_rooms.pop(room.room_id, None)
            else:
                await room.broadcast({
                    "type": "player_left",
                    "playerId": player.player_id,
                    "players": room.get_player_list()
                })


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='YtDlp API Server')
    parser.add_argument('--port', type=int, default=8000, help='Port to run the server on')
    args = parser.parse_args()

    # Fix for PyInstaller --noconsole (sys.stdout/stderr are None)
    # Uvicorn needs valid streams for logging configuration
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    logging.info(f"Starting uvicorn server on port {args.port}...")
    
    # Write PID file
    try:
        with open("server.pid", "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logging.error(f"Failed to write PID file: {e}")

    # IP Display Logic
    try:
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
        logging.info(f"Available IP addresses: {ip_list}")
    except:
        pass

    try:
        # log_config=None prevents uvicorn from using its default config which fails without a console
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_config=None)
    except Exception as e:
        logging.critical(f"Failed to start uvicorn: {e}")
        print(f"CRITICAL ERROR: Failed to start uvicorn: {e}")
        sys.exit(1)

