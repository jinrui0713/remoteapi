import yt_dlp
import httpx
import json
import os

url = "https://www.youtube.com/watch?v=jnLpVzIDNQo"
cookies_path = "cookies.txt"

ydl_opts = {
    'format': 'best[protocol^=http][ext=mp4]/best[protocol^=http]/best[ext=mp4]/best',
    'quiet': True,
}

if os.path.exists(cookies_path):
    # print(f"Using cookies from {cookies_path}")
    ydl_opts['cookiefile'] = cookies_path

print(f"Testing URL: {url}")
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    stream_url = info.get('url')
    headers = info.get('http_headers', {})
    
    print("Base Status Check...")
    with httpx.Client(verify=False, follow_redirects=True) as client:
        r = client.head(stream_url, headers=headers)
        print(f"Base: {r.status_code}")

    print("\nCheck with Range: bytes=0-")
    headers['Range'] = 'bytes=0-'
    with httpx.Client(verify=False, follow_redirects=True) as client:
        r = client.head(stream_url, headers=headers)
        print(f"With Range: {r.status_code}")
