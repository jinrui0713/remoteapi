from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import yt_dlp
import os
import uvicorn
import logging

# ログ設定
logging.basicConfig(
    filename='server.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = FastAPI(title="yt-dlp API Server", version="1.0.0")

# ダウンロード保存先ディレクトリ
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class DownloadRequest(BaseModel):
    url: str
    format: str = "best"

def download_video_task(url: str, format: str):
    """バックグラウンドで動画をダウンロードするタスク"""
    logging.info(f"Starting download: {url}")
    ydl_opts = {
        'format': format,
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        logging.info(f"Download completed: {url}")
    except Exception as e:
        logging.error(f"Error downloading {url}: {e}")

@app.post("/download")
async def download_video(request: DownloadRequest, background_tasks: BackgroundTasks):
    """
    動画のダウンロードをバックグラウンドで開始します。
    """
    if not request.url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    background_tasks.add_task(download_video_task, request.url, request.format)
    return {"message": "Download started in background", "url": request.url}

@app.get("/info")
async def get_info(url: str):
    """
    動画の情報を取得します（ダウンロードはしません）。
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get('title'),
                "duration": info.get('duration'),
                "uploader": info.get('uploader'),
                "view_count": info.get('view_count'),
                "url": info.get('url') # 実際の動画URL
            }
    except Exception as e:
        logging.error(f"Error getting info for {url}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    # ホストを0.0.0.0にすることで外部からのアクセスを許可
    uvicorn.run(app, host="0.0.0.0", port=8000)
