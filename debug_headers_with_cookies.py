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
    print(f"Using cookies from {cookies_path}")
    ydl_opts['cookiefile'] = cookies_path
else:
    print("No cookies.txt found")

print(f"Testing URL: {url}")
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    stream_url = info.get('url')
    headers = info.get('http_headers', {})
    
    print("Stream URL:", stream_url[:100] + "...")
    print("Headers:", json.dumps(headers, indent=2))
    
    # Check if Cookie is in headers
    if 'Cookie' in headers:
        print("Cookie header IS present in info['http_headers']")
    else:
        print("Cookie header IS NOT present in info['http_headers']")

    print("\nAttempting HEAD request with httpx (using provided headers)...")
    try:
        with httpx.Client(verify=False, follow_redirects=True) as client:
            r = client.head(stream_url, headers=headers)
            print(f"Status Code: {r.status_code}")
            
    except Exception as e:
        print(f"Error: {e}")
