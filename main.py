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
    from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request, Response, Depends, Form, Body
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, JSONResponse
    from pydantic import BaseModel
    import yt_dlp
    import uvicorn
    import socket
    import uuid
    import time
    import asyncio
    import secrets
    import hashlib
    import shutil
    import aiofiles # Added for async file reading
    import zipfile # Added for bulk download
    import io 
    from concurrent.futures import ThreadPoolExecutor
    from typing import Dict, List, Optional
    from yt_dlp.utils import sanitize_filename
    import db_utils
    
    # Initialize DB
    db_utils.init_db()
    
    # Import Proxy Module
    from proxy_module import proxy_service
    
    logging.info("Dependencies imported successfully.")
except Exception as e:
    logging.critical(f"Failed to import dependencies: {e}")
    print(f"CRITICAL ERROR: Failed to import dependencies: {e}")
    sys.exit(1)

app = FastAPI(title="yt-dlp API Server", version="8.2.20")

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
        'noplaylist': False, # Changed to False to allow playlist if requested (see below logic)
        # Automatic Cookie Handling: try to load from browser if cookies.txt is missing
        'cookiesfrombrowser': ('chrome', 'edge', 'firefox'),
    }
    
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
        ydl_opts['playlistend'] = 10
        # For playlist, we need unique filenames
        ydl_opts['outtmpl'] = os.path.join(TEMP_DIR, f'{job_id}_%(playlist_index)s.%(ext)s')
    elif "list=" in req.url:
        logging.info(f"URL contains list parameter but treated as single video: {req.url}")

    # Format selection
    if req.type == 'audio':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': req.audio_format,
            'preferredquality': '192',
        }]
    else:
        # Video format - FORCE H.264 (avc1) for maximum compatibility
        ydl_opts['merge_output_format'] = 'mp4'
        
        # Prioritize AVC(h.264) video + AAC audio. Fallback to best mp4, then best.
        # This selector tries to find video with codec starting with 'avc' (h264)
        if req.quality == 'best':
            ydl_opts['format'] = 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        else:
            ydl_opts['format'] = f'bestvideo[vcodec^=avc][height<={req.quality}]+bestaudio[ext=m4a]/bestvideo[height<={req.quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={req.quality}][ext=mp4]/best[height<={req.quality}]'

    try:
        # Wrapper to allow retry logic
        def attempt_download(opts):
            with yt_dlp.YoutubeDL(opts) as ydl:
                 return ydl.extract_info(req.url, download=True)

        try:
            info = attempt_download(ydl_opts)
        except yt_dlp.utils.DownloadError as e:
            err_msg = str(e)
            if ("Sign in to confirm" in err_msg or "downloaded file is empty" in err_msg) and 'cookiefile' in ydl_opts:
                logging.warning(f"Download error detected ({err_msg}). Retrying with browser cookies (Chrome/Edge)...")
                # Fallback: Remove file and use browser. Removed Firefox to avoid keyring issues.
                del ydl_opts['cookiefile']
                ydl_opts['cookiesfrombrowser'] = ('chrome', 'edge')
                info = attempt_download(ydl_opts)
            else:
                raise e
            
        # Update title from final info
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

        job.status = JobStatus.ERROR
        job.error_msg = str(e)
        logging.error(f"Job {job_id} failed: {e}")

# --- API Endpoints ---

@app.get("/")
async def index():
    return FileResponse(os.path.join("static", "index.html"))

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
        ydl_opts = {'format': 'best', 'quiet': True}
        
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
            
            client = httpx.AsyncClient(verify=False, follow_redirects=True)
            req_stream = client.build_request("GET", stream_url)
            r = await client.send(req_stream, stream=True)
            
            return StreamingResponse(
                proxy_service.stream_response(r, request.client.host, limit_bps),
                status_code=r.status_code,
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
        
        # Stream response
        return StreamingResponse(
            proxy_service.stream_response(resp, client_ip, limit_bps),
            media_type=resp.headers.get("content-type", "application/octet-stream"),
            headers={"Content-Disposition": resp.headers.get("Content-Disposition", "")}
        )
    except Exception as e:
        return Response(status_code=404)

@app.post("/proxy")
async def proxy_handler(payload: str = Form(...), request: Request = None):
    try:
        client_ip = request.client.host if request else "unknown"
        
        # Check Blocked IP
        if db_utils.is_ip_blocked(client_ip):
             return Response(content="Access Denied: Your IP is blocked.", status_code=403)

        # Rate Limit Check
        token = request.cookies.get(AUTH_COOKIE_NAME)
        username = None
        role = "guest"
        if token and token in sessions:
            username = sessions[token].get('username')
            role = sessions[token].get('role', 'user')
            
        if username:
            if not check_rate_limit(username, role, 'proxy'):
                return Response(content="API Limit Exceeded: Proxy quota reached for this hour.", status_code=429)
            add_rate_limit_usage(username, 'proxy')

        limit_mb = LIMITS.get(role, {}).get('speed_limit', 0)
        limit_bps = int(limit_mb * 1024 * 1024) if limit_mb > 0 else None

        data = proxy_service.decrypt_payload(payload)
        url = data['url']
        
        # Execute Proxy Request
        resp = await proxy_service.proxy_request(url, client_ip)
        
        # Rewrite HTML if content type is html
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            content = await resp.aread()
            # Log bandwidth for non-streamed content
            db_utils.log_bandwidth(client_ip, len(content), 0, "proxy")
            
            rewritten = proxy_service.rewrite_html(content, url)
            return Response(content=rewritten, media_type="text/html; charset=utf-8")
        else:
            # Stream other content with limit
            return StreamingResponse(
                proxy_service.stream_response(resp, client_ip, limit_bps),
                media_type=content_type,
                headers={"Content-Disposition": resp.headers.get("Content-Disposition", "")}
            )

    except HTTPException as he:
        return Response(content=f"Proxy Error: {he.detail}", status_code=he.status_code, media_type="text/plain; charset=utf-8")
    except Exception as e:
        logging.error(f"Proxy failed: {e}", exc_info=True)
        return Response(content=f"Proxy Internal Error: {str(e)}", status_code=500, media_type="text/plain; charset=utf-8")

@app.get("/proxy")
async def proxy_get_handler():
    """Handle GET requests to /proxy gracefully by redirecting or showing message"""
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

