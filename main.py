import os
import sys
import logging

# Configure logging to both file and console
handlers = [logging.FileHandler('server.log'), logging.StreamHandler(sys.stdout)]
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
    from concurrent.futures import ThreadPoolExecutor
    from typing import Dict, List, Optional
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

app = FastAPI(title="yt-dlp API Server", version="7.2.3")

# --- Middleware for Bandwidth & Fingerprinting ---
@app.middleware("http")
async def monitor_traffic(request: Request, call_next):
    client_ip = request.client.host
    
    # 1. Check Blocked IP
    if db_utils.is_ip_blocked(client_ip):
        return Response(content="Access Denied: Your IP is blocked.", status_code=403)

    # 2. Track Active Clients
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
         db_utils.log_bandwidth(client_ip, req_size, res_size, "api")

    return response

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

# Mount downloads directory for static access (playback)
app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")

# --- Auth & Stats ---
AUTH_COOKIE_NAME = "ytdlp_auth"

# User Credentials
USERS = {
    "admin": "Shogo3170!",
    "user": "0713"
}

# Session Store (Simple in-memory)
# Token -> {exp: timestamp, ip: str, ua_hash: str, role: str}
sessions: Dict[str, Dict] = {}

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
    # Allow static resources and login endpoints
    if request.url.path.startswith("/static") or \
       request.url.path in ["/login", "/api/login", "/system/info"]:
        return await call_next(request)
    
    # Check Auth
    if not check_auth(request):
        # If API request, return 401
        if request.url.path.startswith("/api"):
             return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        # If page request, redirect to login
        return RedirectResponse("/static/login.html")

    client_ip = request.client.host
    
    # Check Blocked IP
    if db_utils.is_ip_blocked(client_ip):
        return JSONResponse(status_code=403, content={"detail": "Access Denied: Your IP is blocked."})

    # Check Role for Bypass
    token = request.cookies.get(AUTH_COOKIE_NAME)
    is_admin = False
    if token and token in sessions and sessions[token].get('role') == 'admin':
        is_admin = True

    # Check Load Limit (for new sessions or heavy endpoints)
    # Exclude Proxy from Load Limit to prevent blocking Admin due to proxy errors
    if not request.url.path.startswith("/proxy") and not request.url.path.startswith("/api/proxy"):
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

# --- Job Management ---

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

def run_download(job_id: str, req: DownloadRequest):
    """Execute download in thread pool"""
    job = jobs.get(job_id)
    if not job:
        return

    logging.info(f"Starting job {job_id}: {req.url}")
    
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
        'noplaylist': True,
    }

    # Subtitle options
    if req.subtitles:
        ydl_opts.update({
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [req.subtitles_lang],
            'embedsubtitles': req.embed_subtitles,
        })

    # Check for cookies.txt
    cookie_file = os.path.join(execution_dir, "cookies.txt")
    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file
        logging.info(f"Using cookies from {cookie_file}")

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
        # Video format
        ydl_opts['merge_output_format'] = 'mp4'
        
        if req.quality == 'best':
            ydl_opts['format'] = 'bestvideo+bestaudio/best'
        else:
            ydl_opts['format'] = f'bestvideo[height<={req.quality}]+bestaudio/best[height<={req.quality}]/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Start download and get info
            info = ydl.extract_info(req.url, download=True)
            
            # Update title from final info
            job.title = info.get('title', job.title)

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
                
                # Sanitize title
                safe_title = sanitize_filename(job.title)
                
                if len(found_files) > 1:
                    # Try to extract index from filename if possible
                    fname = os.path.basename(file_path)
                    try:
                        # job_id_1.mp4 -> 1
                        idx_part = fname.replace(job_id + '_', '').split('.')[0]
                        new_filename = f"{safe_title}_{idx_part}{ext}"
                    except:
                        new_filename = f"{safe_title}_{os.path.basename(file_path)}{ext}"
                else:
                    new_filename = f"{safe_title}{ext}"

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

@app.post("/download")
async def start_download(request: DownloadRequest):
    job_id = str(uuid.uuid4())
    job = DownloadJob(
        id=job_id,
        url=request.url,
        status=JobStatus.QUEUED,
        created_at=time.time()
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
async def list_files():
    files = []
    if os.path.exists(DOWNLOAD_DIR):
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp):
                try:
                    stat = os.stat(fp)
                    files.append({
                        "filename": f,
                        "size": stat.st_size,
                        "created_at": stat.st_ctime
                    })
                except Exception:
                    pass
    # Sort by newest
    files.sort(key=lambda x: x["created_at"], reverse=True)
    return files

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

@app.post("/api/login")
async def login(req: LoginRequest, response: Response, request: Request):
    if req.username in USERS and USERS[req.username] == req.password:
        # Create session
        session_token = secrets.token_urlsafe(32)
        sessions[session_token] = {
            "exp": time.time() + (12 * 3600), # 12 hours
            "role": "admin" if req.username == "admin" else "user",
            "ip": request.client.host
        }
        
        db_utils.log_event(request.client.host, "LOGIN", f"User: {req.username}")
        
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=False, # Set True if HTTPS is guaranteed
            max_age=12 * 3600
        )
        return {"message": "Logged in"}
    else:
        db_utils.log_event(request.client.host, "LOGIN_FAILED", f"User: {req.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

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
        
        # Get Client List (Fingerprints)
        # We need to add a function to db_utils for this, or just query here.
        # Let's add it to db_utils later, for now just raw query or skip if not ready.
        # Actually, let's add it to db_utils now.
        clients = db_utils.get_clients()

        return {
            "active_clients": get_active_client_count(),
            "sessions": len(sessions),
            "clients_list": current_clients,
            "disk_usage": {
                "total": total,
                "used": used,
                "free": free
            },
            "system_info": {
                "platform": sys.platform,
                "python_version": sys.version,
                "cpu_count": os.cpu_count() or 1
            },
            "logs": logs,
            "bandwidth": bandwidth,
            "blocked_ips": blocked_ips,
            "clients": clients
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Admin stats error: {e}", exc_info=True)

@app.post("/api/client/info")
async def client_info(request: Request, info: Dict = Body(...)):
    client_ip = request.client.host
    # Generate a simple fingerprint ID if not provided
    # In a real scenario, we'd use a library or more complex logic.
    # Here we trust the client to send some data, and we hash it + IP.
    
    # Create a unique ID based on the info provided
    fingerprint_str = f"{info.get('ua')}{info.get('screen')}{info.get('depth')}{client_ip}"
    client_id = hashlib.md5(fingerprint_str.encode()).hexdigest()
    
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

        data = proxy_service.decrypt_payload(payload)
        url = data['url']
        
        resp = await proxy_service.proxy_request(url, client_ip)
        
        # Stream response
        return StreamingResponse(
            proxy_service.stream_response(resp, client_ip),
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
            return Response(content=rewritten, media_type="text/html")
        else:
            # Stream other content with limit
            return StreamingResponse(
                proxy_service.stream_response(resp, client_ip),
                media_type=content_type,
                headers={"Content-Disposition": resp.headers.get("Content-Disposition", "")}
            )

    except HTTPException as he:
        return Response(content=f"Proxy Error: {he.detail}", status_code=he.status_code)
    except Exception as e:
        logging.error(f"Proxy failed: {e}", exc_info=True)
        return Response(content=f"Proxy Internal Error: {str(e)}", status_code=500)

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
    """Direct download endpoint"""
    # Security check
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    
    # Log Download Event
    client_ip = request.client.host
    db_utils.log_event(client_ip, "DOWNLOAD", f"File: {filename}")
    
    # Use Proxy Service's streaming with throttling for downloads too
    # We need to wrap the file reading in an async generator compatible with stream_response logic
    # Or just reuse the logic. Let's create a helper in proxy_module or just implement here.
    # Since proxy_module has the state, let's use it.
    
    # We need to open the file and yield chunks
    async def file_iterator(path):
        async with aiofiles.open(path, 'rb') as f:
            while True:
                chunk = await f.read(64 * 1024) # 64KB
                if not chunk:
                    break
                
                # Apply Throttling Logic from Proxy Service
                should_throttle = proxy_service._update_stats(client_ip, len(chunk))
                if should_throttle:
                    # 1MB/s limit
                    await asyncio.sleep(len(chunk) / (1024 * 1024))
                
                yield chunk
                
    # Log Bandwidth (Approximate)
    file_size = os.path.getsize(file_path)
    db_utils.log_bandwidth(client_ip, 0, file_size, "download")

    return StreamingResponse(
        file_iterator(file_path), 
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

@app.get("/system/info")
async def system_info(request: Request):
    """Get system status and load info"""
    active_jobs = len([j for j in jobs.values() if j.status in [JobStatus.QUEUED, JobStatus.DOWNLOADING]])
    
    # Determine role
    role = "guest"
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token and token in sessions:
        role = sessions[token].get("role", "user")

    return {
        "hostname": socket.gethostname(),
        "active_jobs": active_jobs,
        "active_clients": get_active_client_count(),
        "platform": sys.platform,
        "version": app.version,
        "role": role
    }

@app.post("/api/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token and token in sessions:
        del sessions[token]
    
    db_utils.log_event(request.client.host, "LOGOUT", "")
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"message": "Logged out"}

@app.post("/system/cookies")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload cookies.txt file"""
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
app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")

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

