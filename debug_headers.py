import yt_dlp
import json

url = "https://www.youtube.com/watch?v=jnLpVzIDNQo" # The video from the log

ydl_opts = {
    'format': 'best[protocol^=http][ext=mp4]/best[protocol^=http]/best[ext=mp4]/best',
    'quiet': True,
}

print(f"Testing URL: {url}")
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    print("Stream URL:", info.get('url'))
    print("HTTP Headers:", json.dumps(info.get('http_headers', {}), indent=2))
