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
    from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
    import yt_dlp
    import uvicorn
    import socket
    import uuid
    import time
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from typing import Dict, List, Optional
    logging.info("Dependencies imported successfully.")
except Exception as e:
    logging.critical(f"Failed to import dependencies: {e}")
    print(f"CRITICAL ERROR: Failed to import dependencies: {e}")
    sys.exit(1)

app = FastAPI(title="yt-dlp API Server", version="2.0.0")

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

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Mount downloads directory for static access (playback)
app.mount("/downloads", StaticFiles(directory=DOWNLOAD_DIR), name="downloads")

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
    
    # Build yt-dlp options
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
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

            # Verify existence
            if final_filename and os.path.exists(final_filename):
                job.filename = os.path.basename(final_filename)
            else:
                # Critical fallback: Search directory for the file
                # Since we use restrictfilenames, the filename should be predictable
                # But if extension changed...
                logging.warning(f"File not found at {final_filename}, searching in {DOWNLOAD_DIR}")
                
                # Try to find a file that matches the title (sanitized)
                # This is hard because we don't know exactly how it was sanitized
                # But we can check if job.filename (from progress hook) exists
                if job.filename:
                    potential_path = os.path.join(DOWNLOAD_DIR, job.filename)
                    if os.path.exists(potential_path):
                        logging.info(f"Found file using progress hook filename: {job.filename}")
                        # Keep job.filename as is
                    else:
                        # Try with mp4 extension if video
                        if req.type == 'video':
                            base, _ = os.path.splitext(job.filename)
                            mp4_path = os.path.join(DOWNLOAD_DIR, base + ".mp4")
                            if os.path.exists(mp4_path):
                                job.filename = os.path.basename(mp4_path)
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
async def system_info():
    """Get system status and load info"""
    active_jobs = len([j for j in jobs.values() if j.status in [JobStatus.QUEUED, JobStatus.DOWNLOADING]])
    return {
        "hostname": socket.gethostname(),
        "active_jobs": active_jobs,
        "platform": sys.platform,
        "version": app.version
    }

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

