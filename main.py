from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
import os
import sys
import uvicorn
import logging
import socket
import uuid
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

# ログ設定
logging.basicConfig(
    filename='server.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

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

# ffmpeg設定
ffmpeg_exe = os.path.join(bundle_dir, "ffmpeg.exe")
if os.path.exists(ffmpeg_exe):
    logging.info(f"Found bundled ffmpeg at: {ffmpeg_exe}")
    os.environ["PATH"] += os.pathsep + bundle_dir

# ダウンロード保存先
DOWNLOAD_DIR = os.path.join(execution_dir, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
    quality: str = "best" # best, 1080, 720, 480
    audio_format: str = "mp3" # mp3, m4a, wav

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
    }

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
        if req.quality == 'best':
            ydl_opts['format'] = 'bestvideo+bestaudio/best'
        else:
            # Try to get specific height, fallback to best
            ydl_opts['format'] = f'bestvideo[height<={req.quality}]+bestaudio/best[height<={req.quality}]/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get title first
            info = ydl.extract_info(req.url, download=False)
            job.title = info.get('title')
            
            # Start download
            ydl.download([req.url])
            
        job.status = JobStatus.FINISHED
        logging.info(f"Job {job_id} completed")
        
    except Exception as e:
        job.status = JobStatus.ERROR
        job.error_msg = str(e)
        logging.error(f"Job {job_id} failed: {e}")

# --- API Endpoints ---

@app.get("/")
async def index():
    return FileResponse(os.path.join("static", "index.html"))

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

@app.get("/info")
async def get_info(url: str):
    """Get video info (no download)"""
    ydl_opts = {'quiet': True, 'no_warnings': True}
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

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    # IP Display Logic
    try:
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
        print(f"Available IP addresses: {ip_list}")
    except:
        pass

    uvicorn.run(app, host="0.0.0.0", port=8000)

