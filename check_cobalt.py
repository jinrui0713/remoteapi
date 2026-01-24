import requests
import time

instances = [
    "https://api.cobalt.tools",
    "https://co.wuk.sh",
    "https://api.wuk.sh",
    "https://cobalt.bowring.uk",
    "https://save.lovely.rest",
    "https://cobalt.steamodded.com",
    "https://dl.khub.win",
    "https://cobalt.tools"
]

print("Checking Cobalt instances...")

headers = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

working_instances = []

for url in instances:
    api_url = url
    if not url.endswith('/api') and 'api' not in url:
        api_url = f"{url}/api/json" # Try common endpoint
    
    try:
        # Cobalt usually has a health check or basic info at /api/serverInfo or just /json on POST
        # Let's try to simulate a simple request or just check the root/health
        
        # Method 1: GET /api/serverInfo (some versions)
        response = requests.get(f"{url}/api/serverInfo", headers=headers, timeout=5)
        
        if response.status_code == 200:
             print(f"[OK] {url} (serverInfo)")
             working_instances.append(url)
             continue
             
        # Method 2: POST /api/json (Standard)
        # We won't actually download, just send an empty body or bad request to see if it responds as Cobalt
        response = requests.post(f"{url}/api/json", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, headers=headers, timeout=5)
        
        if response.status_code in [200, 400, 429, 500]: # If it replies, it's likely a cobalt instance
             print(f"[OK] {url} (Status: {response.status_code})")
             working_instances.append(url)
        else:
             print(f"[FAIL] {url} (Status: {response.status_code})")
             
    except Exception as e:
        print(f"[ERR] {url}: {e}")

print("\nWorking Instances:")
for i in working_instances:
    print(i)
