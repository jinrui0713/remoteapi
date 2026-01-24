import httpx
import logging
import asyncio
import json
from typing import Optional, Dict, Any

logger = logging.getLogger("server")

async def get_savefrom(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for SaveFrom.net"""
    try:
        # SaveFrom worker endpoint
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
        
        # logger.info(f"Checking SaveFrom for {url}")
        resp = await client.post(worker_url, data=data, headers=headers)
        if resp.status_code != 200:
            return None
            
        # The response is usually JSON
        try:
             json_data = resp.json()
        except:
             # Sometimes they send multiple JSON objects or text
             return None
        
        if "url" in json_data and isinstance(json_data["url"], list):
            # Select best quality
            best_link = None
            for item in json_data["url"]:
                if item.get("type") == "mp4":
                    best_link = item.get("url")
                    # Break if we find a good one? Or prefer HD?
                    # They are usually sorted or we can just pick first mp4
                    break
            
            if best_link:
                 title = json_data.get("meta", {}).get("title", f"savefrom_{hash(url)}")
                 return {
                     "base_url": "https://savefrom.net",
                     "data": {"filename": f"{title}.mp4"},
                     "download_url": best_link,
                     "source": "SaveFrom"
                 }
        return None
    except Exception as e:
        # logger.warning(f"SaveFrom Error: {e}")
        return None

async def get_y2mate(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for Y2Mate (Alternative to generic ytdown)"""
    try:
        # 1. Analyze
        analyze_url = "https://www.y2mate.com/mates/analyzeV2/ajax"
        headers = {
            "Origin": "https://www.y2mate.com",
            "Referer": "https://www.y2mate.com/en858",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        data = {
            "k_query": url,
            "k_page": "home",
            "hl": "en",
            "q_auto": "0"
        }
        
        # logger.info(f"Checking Y2Mate for {url}")
        resp = await client.post(analyze_url, data=data, headers=headers)
        
        try:
            data_json = resp.json()
        except:
            return None
            
        if data_json.get("status") != "ok":
            return None
            
        vid = data_json.get("vid")
        links = data_json.get("links", {}).get("mp4", {})
        title = data_json.get("title", f"y2mate_{hash(url)}")
        
        if not vid:
             return None
             
        # Find best quality key
        best_k = None
        # links is a dict like {"136": {"k": "...", "q": "720p"}, ...}
        # Sort by resolution?
        # Just pick 720p or 1080p if available
        for k_id, info in links.items():
            q = info.get("q", "")
            if "1080" in q or "720" in q:
                best_k = info.get("k")
                break
        
        if not best_k and links:
             # Fallback to first
             first_key = list(links.keys())[0]
             best_k = links[first_key].get("k")
             
        if not best_k:
            return None
            
        # 2. Convert
        convert_url = "https://www.y2mate.com/mates/convertV2/index"
        data_conv = {
            "vid": vid,
            "k": best_k
        }
        
        resp_conv = await client.post(convert_url, data=data_conv, headers=headers)
        data_c = resp_conv.json()
        
        if data_c.get("status") != "ok":
             return None
             
        dlink = data_c.get("dlink")
        if dlink:
             return {
                 "base_url": "https://y2mate.com",
                 "data": {"filename": f"{title}.mp4"},
                 "download_url": dlink,
                 "source": "Y2Mate"
             }
        return None
    except Exception as e:
        # logger.warning(f"Y2Mate Error: {e}")
        return None

async def get_10downloader(url: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """Scraper for 10downloader (YouTube mostly)"""
    # Simply inspects the page usually. 
    # Skipping complex ones for now.
    pass
