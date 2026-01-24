import httpx
import logging
import asyncio
import json
import re
from typing import Optional, Dict, Any

logger = logging.getLogger("server")

async def get_savefrom(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for SaveFrom.net"""
    try:
        # SaveFrom endpoint
        worker_url = "https://worker.sf-tools.com/savefrom.php"
        headers = {
            "Origin": "https://en.savefrom.net",
            "Referer": "https://en.savefrom.net/",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        data = {
            "sf_url": url,
            "sf_submit": "",
            "new": "2",
            "lang": "en",
            "app": "",
            "country": "us",
            "os": "Windows",
            "browser": "Chrome",
            "channel": "main",
            "sf-nomad": "1"
        }
        
        resp = await client.post(worker_url, data=data, headers=headers)
        if resp.status_code != 200:
            return None

        text = resp.text
        # Check for direct googlevideo links
        # Regex to find links inside the JS blob
        # Look for "url":"https:..." or just http...googlevideo...
        
        # Pattern 1: JSON-like structure inside
        # often inside `g_get_video(...)` or similar
        
        # Simple extraction of googlevideo links
        # Note: SaveFrom often escapes slashes like https:\/\/
        
        video_links = re.findall(r'(https?:\\?\/\\?\/[^\s"\'<>]+\.googlevideo\.com\\?[^\s"\'<>]*)', text)
        
        if video_links:
            # Clean up slashes
            best_link = video_links[0].replace('\\/', '/')
            
            # Extract title if possible (regex)
            title_match = re.search(r'"title":"([^"]+)"', text)
            title = title_match.group(1) if title_match else f"savefrom_{hash(url)}"
            # decode unicode
            try:
                title = bytes(title, 'utf-8').decode('unicode_escape')
            except: pass
            
            return {
                "base_url": "https://savefrom.net",
                "data": {"filename": f"{title}.mp4"},
                "download_url": best_link,
                "source": "SaveFrom"
            }
            
        return None
    except Exception as e:
        return None

async def get_loader_to(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for Loader.to (Powering many sites)"""
    try:
        # 1. Start Job
        create_url = "https://loader.to/ajax/download.php"
        params = {
            "format": "1080", 
            "url": url,
            "start": "1",
            "end": "1"
        }
        headers = {
             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
             "Referer": "https://loader.to/",
             "Origin": "https://loader.to"
        }
        
        resp = await client.get(create_url, params=params, headers=headers)
        if resp.status_code != 200:
             return None
             
        data = resp.json()
        if not data.get("success"):
             return None
             
        job_id = data.get("id")
        if not job_id:
             return None
             
        # 2. Poll for Status (Limit retries to avoid long wait)
        poll_url = "https://loader.to/ajax/progress.php"
        for _ in range(10): # Max 10 checks (approx 10-20 sec)
            await asyncio.sleep(1.5)
            p_resp = await client.get(poll_url, params={"id": job_id}, headers=headers)
            p_data = p_resp.json()
            
            if p_data.get("success") == 1:
                # Finished
                dlink = p_data.get("download_url")
                if dlink:
                     return {
                         "base_url": "https://loader.to",
                         "data": {"filename": f"loader_{job_id}.mp4"},
                         "download_url": dlink,
                         "source": "Loader.to"
                     }
                break # Failed to get link even if success?
                
        return None

    except Exception as e:
        # logger.warning(f"Loader.to Error: {e}")
        return None

# Alias function to match previous interface if needed, or update main
get_y2mate = get_loader_to # Fallback/Replace implementation

async def get_10downloader(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for 10downloader (YouTube mostly)"""
    # Simply inspects the page usually. 
    # Skipping complex ones for now.
    pass
