import httpx
import time
import sys

URL = "http://127.0.0.1:8000/download"
TARGET_VIDEO = "https://youtu.be/08q2Tt3p5CU"

def trigger_download():
    print(f"Triggering download for {TARGET_VIDEO}")
    try:
        # Use a timeout of 10s for the request, though the job is async
        r = httpx.post(
            URL, 
            json={
                "url": TARGET_VIDEO,
                "type": "video",
                "quality": "best"
            },
            timeout=30.0,
            follow_redirects=True
        )
        print(f"Response Status: {r.status_code}")
        print(f"Response Body: {r.text}")
        
        if r.status_code == 200:
            data = r.json()
            job_id = data.get("job_id")
            print(f"Job ID: {job_id}")
            return job_id
        return None
    except Exception as e:
        print(f"Error triggering download: {e}")
        return None

def check_status(job_id):
    if not job_id: return
    status_url = f"http://127.0.0.1:8000/jobs/{job_id}"
    print(f"Checking status for {job_id}...")
    
    while True:
        try:
            r = httpx.get(status_url, follow_redirects=True)
            if r.status_code != 200:
                print(f"Error getting status: {r.status_code} {r.text}")
                time.sleep(2)
                continue
                
            data = r.json()
            status = data.get("status")
            progress = data.get("progress")
            error = data.get("error_msg")
            
            print(f"Status: {status}, Progress: {progress}%")
            
            if status == "finished":
                print("Download Finished!")
                print(f"Output Filename: {data.get('filename')}")
                break
            elif status == "error":
                print(f"Download Failed: {error}")
                break
            
            time.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error checking status: {e}")
            time.sleep(2)

if __name__ == "__main__":
    job_id = trigger_download()
    check_status(job_id)
