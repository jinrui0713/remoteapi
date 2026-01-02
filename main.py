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
    from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request, Response, Depends, Form
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
    from concurrent.futures import ThreadPoolExecutor
    from typing import Dict, List, Optional
    
    # Import Proxy Module
    from proxy_module import proxy_service
    
    logging.info("Dependencies imported successfully.")
except Exception as e:
    logging.critical(f"Failed to import dependencies: {e}")
    print(f"CRITICAL ERROR: Failed to import dependencies: {e}")
    sys.exit(1)

app = FastAPI(title="yt-dlp API Server", version="6.0.2")

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
else:
    DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), ".YtDlpApiServer", "downloads")

# Temp directory for processing
TEMP_DIR = os.path.join(os.path.dirname(DOWNLOAD_DIR), 'temp')
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
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
MAX_CLIENTS = 3
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

    # Check Load Limit (for new sessions or heavy endpoints)
    client_ip = request.client.host
    active_clients[client_ip] = time.time()
    
    if get_active_client_count() > MAX_CLIENTS:
        # Allow existing sessions? For now, strict limit on count
        # But we just added them to active_clients, so if they are the 4th, they get blocked?
        # Let's say if they were NOT in active_clients before and now count > MAX
        # But we just added them. So if count > MAX, we block.
        # To be nice, we should probably allow admin or check if this IP was already active.
        # Simple logic: If count > MAX, show busy page.
        # But we need to allow the 3 active users to continue.
        # The cleanup logic runs in get_active_client_count.
        pass

    if get_active_client_count() > MAX_CLIENTS:
         # Check if this specific IP was already active (it is, we just updated it)
         # We need to know if it's a *new* client pushing us over.
         # For simplicity, if total > MAX, we reject. 
         # This might block existing users if a 4th one spams. 
         # Better: Track "session start" time.
         return Response(content="現在アクセスが集中しているため、サーバー負荷軽減のためアクセスを制限しています", status_code=503)

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
    ydl_opts = {
        'outtmpl': os.path.join(TEMP_DIR, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: progress_hook(d, job_id)],
        'writethumbnail': False,
        'restrictfilenames': True, # Ensure filenames are safe (ASCII, no spaces)
        'windowsfilenames': True, # Force Windows-compatible filenames
        'noplaylist': True, # Default to single video to prevent accidental playlist downloads
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
    # Only enable playlist if it is explicitly a playlist URL, not a video in a playlist
    if "playlist?list=" in req.url:
        logging.info(f"Explicit playlist URL detected: {req.url}. Enabling playlist mode (limit 10).")
        ydl_opts['noplaylist'] = False
        ydl_opts['playlistend'] = 10
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
        # Force MP4 merge to ensure browser compatibility and predictable extension
        ydl_opts['merge_output_format'] = 'mp4'
        
        if req.quality == 'best':
            ydl_opts['format'] = 'bestvideo+bestaudio/best'
        else:
            # Try to get specific height, fallback to best
            ydl_opts['format'] = f'bestvideo[height<={req.quality}]+bestaudio/best[height<={req.quality}]/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Start download and get info
            info = ydl.extract_info(req.url, download=True)
            
            # Update title from final info
            job.title = info.get('title', job.title)

            # Determine final filename
            final_filename = None
            
            # Check requested_downloads (populated when merging/converting)
            if 'requested_downloads' in info:
                for d in info['requested_downloads']:
                    if 'filepath' in d:
                        final_filename = d['filepath']
                        # If we found a filepath, break. 
                        # Usually the last one is the merged one? 
                        # Actually requested_downloads is a list of downloads.
                        # If merging, it might contain video and audio.
                        # But the 'filepath' in the info dict itself might be the merged one?
            
            if not final_filename:
                final_filename = info.get('filepath')
            
            if not final_filename:
                # Fallback to prepare_filename (might be wrong extension if converted)
                final_filename = ydl.prepare_filename(info)

            # Verify existence and move to DOWNLOAD_DIR
            if final_filename and os.path.exists(final_filename):
                filename_only = os.path.basename(final_filename)
                dest_path = os.path.join(DOWNLOAD_DIR, filename_only)
                
                # Move file
                import shutil
                shutil.move(final_filename, dest_path)
                logging.info(f"Moved finished file to {dest_path}")
                
                job.filename = filename_only
            else:
                # Critical fallback: Search directory for the file
                # Since we use restrictfilenames, the filename should be predictable
                # But if extension changed...
                logging.warning(f"File not found at {final_filename}, searching in {TEMP_DIR}")
                
                # Try to find a file that matches the title (sanitized)
                # This is hard because we don't know exactly how it was sanitized
                # But we can check if job.filename (from progress hook) exists
                if job.filename:
                    potential_path = os.path.join(TEMP_DIR, job.filename)
                    if os.path.exists(potential_path):
                        logging.info(f"Found file using progress hook filename: {job.filename}")
                        dest_path = os.path.join(DOWNLOAD_DIR, job.filename)
                        shutil.move(potential_path, dest_path)
                        # Keep job.filename as is
                    else:
                        # Try with mp4 extension if video
                        if req.type == 'video':
                            base, _ = os.path.splitext(job.filename)
                            mp4_path = os.path.join(TEMP_DIR, base + ".mp4")
                            if os.path.exists(mp4_path):
                                job.filename = os.path.basename(mp4_path)
                                dest_path = os.path.join(DOWNLOAD_DIR, job.filename)
                                shutil.move(mp4_path, dest_path)
                                logging.info(f"Found file with mp4 extension: {job.filename}")
                            else:
                                job.error_msg = "File missing after download"
                                job.status = JobStatus.ERROR
                                return
                else:
                     job.error_msg = "Could not determine filename"
                     job.status = JobStatus.ERROR
                     return

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
async def login(req: LoginRequest, response: Response):
    if req.username in USERS and USERS[req.username] == req.password:
        # Create session
        session_token = secrets.token_urlsafe(32)
        sessions[session_token] = {
            "exp": time.time() + (12 * 3600), # 12 hours
            "ip": "unknown", # Will be updated on first request
            "ua_hash": "unknown",
            "role": "admin" if req.username == "admin" else "user"
        }
        
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=False, # Set True if HTTPS is guaranteed
            max_age=12 * 3600
        )
        return {"message": "Logged in"}
    else:
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    # Check Auth & Role
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or token not in sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if sessions[token].get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Forbidden")

    # Get Disk Usage
    total, used, free = shutil.disk_usage(DOWNLOAD_DIR)
    
    return {
        "active_clients": get_active_client_count(),
        "sessions": len(sessions),
        "clients_list": active_clients,
        "disk_usage": {
            "total": total,
            "used": used,
            "free": free
        },
        "system_info": {
            "platform": sys.platform,
            "python_version": sys.version,
            "cpu_count": os.cpu_count()
        }
    }

# --- Proxy Endpoints ---

class ProxyEncryptRequest(BaseModel):
    url: str

@app.post("/api/proxy/encrypt")
async def proxy_encrypt(req: ProxyEncryptRequest):
    payload = proxy_service.encrypt_payload(req.url)
    return {"payload": payload}

@app.post("/proxy")
async def proxy_handler(payload: str = Form(...), request: Request = None):
    try:
        data = proxy_service.decrypt_payload(payload)
        url = data['url']
        
        # Execute Proxy Request
        resp = await proxy_service.proxy_request(url)
        
        # Rewrite HTML if content type is html
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            content = await resp.read()
            rewritten = proxy_service.rewrite_html(content, url)
            return Response(content=rewritten, media_type="text/html")
        else:
            # Stream other content with limit
            return StreamingResponse(
                proxy_service.stream_response(resp),
                media_type=content_type,
                headers={"Content-Disposition": resp.headers.get("Content-Disposition", "")}
            )

    except Exception as e:
        logging.error(f"Proxy failed: {e}")
        return Response(content="Proxy Error", status_code=500)

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
async def download_file(filename: str):
    """Direct download endpoint"""
    # Security check
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    
    return FileResponse(file_path, media_type="application/octet-stream", filename=filename)

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
async def logout(response: Response):
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

