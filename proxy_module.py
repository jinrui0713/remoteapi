import os
import time
import json
import base64
import secrets
import logging
import asyncio
from typing import Optional, Dict
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from bs4 import BeautifulSoup
import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import db_utils

# Configuration
PROXY_KEY = secrets.token_bytes(32) # In production, this should be persistent
MAX_SPEED_BPS = 1024 * 1024 # 1MB/s (Throttled speed)
BURST_THRESHOLD_BPS = 1.5 * 1024 * 1024 # 1.5MB/s (Trigger threshold)
BURST_DURATION = 2.0 # Seconds
CHUNK_SIZE = 64 * 1024 # 64KB

class ProxyService:
    def __init__(self):
        self.aesgcm = AESGCM(PROXY_KEY)
        self.client = httpx.AsyncClient(
            verify=False, 
            follow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        # Track client bandwidth usage: IP -> {window_start: float, bytes: int, throttled_until: float}
        self.client_stats: Dict[str, Dict] = {}

    def _update_stats(self, client_ip: str, chunk_size: int) -> bool:
        """
        Updates stats and returns True if request should be throttled.
        """
        now = time.time()
        if client_ip not in self.client_stats:
            self.client_stats[client_ip] = {
                "window_start": now,
                "bytes": 0,
                "throttled_until": 0
            }
        
        stats = self.client_stats[client_ip]
        
        # Check if currently throttled
        if now < stats["throttled_until"]:
            return True
            
        # Update Window
        if now - stats["window_start"] > BURST_DURATION:
            # Calculate speed in previous window
            duration = now - stats["window_start"]
            speed = stats["bytes"] / duration
            
            # Reset window
            stats["window_start"] = now
            stats["bytes"] = 0
            
            # Check if we exceeded threshold
            if speed > BURST_THRESHOLD_BPS:
                # Throttle for next window (e.g. 5 seconds)
                stats["throttled_until"] = now + 5.0
                return True
        
        stats["bytes"] += chunk_size
        return False

    def encrypt_payload(self, url: str, exp_seconds: int = 60) -> str:
        nonce = secrets.token_hex(8)
        exp = int(time.time() * 1000) + (exp_seconds * 1000)
        data = json.dumps({
            "url": url,
            "exp": exp,
            "nonce": nonce
        }).encode('utf-8')
        
        iv = secrets.token_bytes(12)
        ciphertext = self.aesgcm.encrypt(iv, data, None)
        
        # Format: IV + Ciphertext
        combined = iv + ciphertext
        return base64.urlsafe_b64encode(combined).decode('utf-8')

    def decrypt_payload(self, payload: str) -> Dict:
        try:
            combined = base64.urlsafe_b64decode(payload)
            iv = combined[:12]
            ciphertext = combined[12:]
            
            plaintext = self.aesgcm.decrypt(iv, ciphertext, None)
            data = json.loads(plaintext.decode('utf-8'))
            
            # Check expiration
            if data['exp'] < time.time() * 1000:
                raise ValueError("Expired")
                
            return data
        except Exception as e:
            logging.error(f"Decryption failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid payload")

    async def proxy_request(self, url: str, client_ip: str = "unknown"):
        # Security checks
        if not (url.startswith("http://") or url.startswith("https://")):
            # Try to fix protocol if missing (though frontend should handle this)
            if not url.startswith("http"):
                 url = "https://" + url
        
        # Basic localhost check (naive)
        if "localhost" in url or "127.0.0.1" in url:
             raise HTTPException(status_code=403, detail="Access denied")

        try:
            db_utils.log_event(client_ip, "PROXY_ACCESS", url)
            # Use shared client
            req = self.client.build_request("GET", url)
            r = await self.client.send(req, stream=True)
            return r
        except Exception as e:
            logging.error(f"Proxy request failed: {e}")
            # Re-initialize client if it might be broken
            if "closed" in str(e).lower() or "pool" in str(e).lower():
                 self.client = httpx.AsyncClient(
                    verify=False, 
                    follow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                )
            raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

    def rewrite_html(self, html_content: bytes, base_url: str) -> str:
        soup = BeautifulSoup(html_content, 'html.parser')
        from urllib.parse import urljoin
        
        # Inject JS for POST navigation and Dynamic Resource Loading
        script = soup.new_tag('script')
        script.string = """
        async function proxyGo(url) {
            try {
                const res = await fetch('/api/proxy/encrypt', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: url})
                });
                const data = await res.json();
                
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/proxy';
                
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'payload'; // Form data key
                input.value = data.payload;
                
                form.appendChild(input);
                document.body.appendChild(form);
                form.submit();
            } catch (e) {
                alert('Navigation failed');
            }
        }

        // Dynamic Resource Rewriter
        document.addEventListener("DOMContentLoaded", function() {
            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType === 1) { // Element
                            // Check img src
                            if (node.tagName === 'IMG' && node.src && !node.src.includes(window.location.host)) {
                                rewriteResource(node, 'src');
                            }
                            // Check script src
                            if (node.tagName === 'SCRIPT' && node.src && !node.src.includes(window.location.host)) {
                                rewriteResource(node, 'src');
                            }
                            // Check link href
                            if (node.tagName === 'LINK' && node.href && !node.href.includes(window.location.host)) {
                                rewriteResource(node, 'href');
                            }
                        }
                    });
                });
            });
            
            observer.observe(document.body, { childList: true, subtree: true });
        });

        async function rewriteResource(node, attr) {
            const originalUrl = node[attr];
            try {
                const res = await fetch('/api/proxy/encrypt', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url: originalUrl})
                });
                const data = await res.json();
                node[attr] = `/api/proxy/resource?payload=${data.payload}`;
            } catch (e) {
                console.error('Failed to rewrite resource:', originalUrl);
            }
        }
        """
        if soup.head:
            soup.head.append(script)
        else:
            soup.append(script)

        # Rewrite links
        for tag in soup.find_all(['a', 'link', 'script', 'img', 'iframe', 'form']):
            # Handle href (Navigation)
            if tag.name == 'a' and tag.has_attr('href'):
                url = tag['href']
                # Resolve relative URLs
                full_url = urljoin(base_url, url)
                
                # Check if it's a valid http/https link (not javascript:, mailto:, etc)
                if full_url.startswith(('http://', 'https://')):
                    tag['href'] = '#'
                    tag['onclick'] = f"proxyGo('{full_url}'); return false;"
            
            # Handle src (Resources)
            if tag.has_attr('src'):
                src = tag['src']
                full_src = urljoin(base_url, src)
                if full_src.startswith(('http://', 'https://')):
                    payload = self.encrypt_payload(full_src, exp_seconds=300)
                    tag['src'] = f"/api/proxy/resource?payload={payload}"
            
            # Handle link href (CSS, Favicons)
            if tag.name == 'link' and tag.has_attr('href'):
                href = tag['href']
                full_href = urljoin(base_url, href)
                if full_href.startswith(('http://', 'https://')):
                    payload = self.encrypt_payload(full_href, exp_seconds=300)
                    tag['href'] = f"/api/proxy/resource?payload={payload}"
            
            # Handle Form Actions
            if tag.name == 'form' and tag.has_attr('action'):
                action = tag['action']
                full_action = urljoin(base_url, action)
                if full_action.startswith(('http://', 'https://')):
                    # Rewrite to use proxyGo (via onsubmit interception if possible, 
                    # but simple forms are hard to proxy with just HTML rewriting.
                    # Best effort: change action to # and use JS to submit via proxy)
                    tag['action'] = '#'
                    tag['onsubmit'] = f"event.preventDefault(); proxyGo('{full_action}' + '?' + new URLSearchParams(new FormData(this)).toString());"

        # Inject Back Button
        if soup.body:
            back_btn = soup.new_tag('div')
            back_btn['style'] = "position: fixed; bottom: 20px; right: 20px; z-index: 2147483647; background: rgba(0,0,0,0.7); color: white; padding: 10px; border-radius: 50%; cursor: pointer; width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; font-weight: bold; font-family: sans-serif; box-shadow: 0 4px 6px rgba(0,0,0,0.3); font-size: 24px;"
            back_btn.string = "←"
            back_btn['onclick'] = "window.history.back()"
            back_btn['title'] = "Go Back"
            soup.body.append(back_btn)

        return str(soup)

    async def stream_response(self, response: httpx.Response, client_ip: str = "unknown"):
        try:
            total_bytes = 0
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                size = len(chunk)
                total_bytes += size
                yield chunk
                
                # Rate limiting Logic
                # Check if we need to throttle based on recent usage
                should_throttle = self._update_stats(client_ip, size)
                
                if should_throttle:
                    # Enforce MAX_SPEED_BPS (1MB/s)
                    expected_time = size / MAX_SPEED_BPS
                    await asyncio.sleep(expected_time)
                else:
                    # No sleep = Max speed
                    pass
            
            # Log bandwidth after stream finishes
            db_utils.log_bandwidth(client_ip, total_bytes, 0, "proxy")
        except Exception as e:
            logging.error(f"Stream error: {e}")
        finally:
            await response.aclose()

proxy_service = ProxyService()
