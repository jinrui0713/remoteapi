import requests
import time

# サーバーのURL（Cloudflare TunnelのURLに書き換えてください）
# 例: BASE_URL = "https://xxxx-xxxx.trycloudflare.com"
BASE_URL = "http://localhost:8000" 

def test_info(video_url):
    print(f"\n[INFO] Getting info for: {video_url}")
    try:
        response = requests.get(f"{BASE_URL}/info", params={"url": video_url})
        if response.status_code == 200:
            data = response.json()
            print("Success!")
            print(f"Title: {data.get('title')}")
            print(f"Duration: {data.get('duration')}s")
            print(f"Uploader: {data.get('uploader')}")
        else:
            print(f"Failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error: {e}")

def test_download(video_url):
    print(f"\n[DOWNLOAD] Requesting download for: {video_url}")
    try:
        payload = {"url": video_url, "format": "best"}
        response = requests.post(f"{BASE_URL}/download", json=payload)
        if response.status_code == 200:
            print("Success! Download started in background.")
            print(f"Response: {response.json()}")
        else:
            print(f"Failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # テスト用動画URL（著作権フリーの素材など）
    TEST_URL = "https://www.youtube.com/watch?v=BaW_jenozKc" # YouTube Help channel video
    
    print(f"Target Server: {BASE_URL}")
    print("1. Testing /info endpoint...")
    test_info(TEST_URL)
    
    print("\n2. Testing /download endpoint...")
    test_download(TEST_URL)
