
import yt_dlp
import os

url = "https://www.youtube.com/watch?v=jNQXAC9IVRw" # Me at the zoo
options = {
    'outtmpl': 'test_download/%(title)s.%(ext)s',
    'quiet': True,
    'format': 'bestvideo+bestaudio/best',
}

with yt_dlp.YoutubeDL(options) as ydl:
    info = ydl.extract_info(url, download=True)
    print("Keys:", info.keys())
    if 'requested_downloads' in info:
        print("Requested Downloads:", info['requested_downloads'])
    print("Filename from prepare_filename:", ydl.prepare_filename(info))
