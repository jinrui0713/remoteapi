import yt_dlp
import httpx
import json

url = "https://www.youtube.com/watch?v=jnLpVzIDNQo"

ydl_opts = {
    'format': 'best[protocol^=http][ext=mp4]/best[protocol^=http]/best[ext=mp4]/best',
    'quiet': True,
}

print(f"Testing URL: {url}")
with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    stream_url = info.get('url')
    headers = info.get('http_headers', {})
    
    print("Stream URL:", stream_url[:100] + "...")
    print("Headers:", json.dumps(headers, indent=2))
    
    print("\nAttempting HEAD request with httpx...")
    try:
        with httpx.Client(verify=False, follow_redirects=True) as client:
            r = client.head(stream_url, headers=headers)
            print(f"Status Code: {r.status_code}")
            
            if r.status_code == 403:
                print("403 Forbidden!")
                # Diagnostic: Try adding Referer
                headers['Referer'] = 'https://www.youtube.com/'
                r2 = client.head(stream_url, headers=headers)
                print(f"With Referer: {r2.status_code}")
                
    except Exception as e:
        print(f"Error: {e}")
