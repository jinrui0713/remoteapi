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
MAX_SPEED_BPS = 1024 * 1024 # 1MB/s
CHUNK_SIZE = 64 * 1024 # 64KB

class ProxyService:
    def __init__(self):
        self.aesgcm = AESGCM(PROXY_KEY)
        self.client = httpx.AsyncClient(
            verify=False, 
            follow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )

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
            req = self.client.build_request("GET", url)
            r = await self.client.send(req, stream=True)
            return r
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

    def rewrite_html(self, html_content: bytes, base_url: str) -> str:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Inject JS for POST navigation
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
        """
        if soup.head:
            soup.head.append(script)
        else:
            soup.append(script)

        # Rewrite links
        for tag in soup.find_all(['a', 'link', 'script', 'img', 'iframe']):
            # Handle href (Navigation)
            if tag.name == 'a' and tag.has_attr('href'):
                url = tag['href']
                if url.startswith('http'):
                    tag['href'] = '#'
                    tag['onclick'] = f"proxyGo('{url}'); return false;"
            
            # Handle src (Resources)
            # We rewrite src to a GET proxy endpoint for resources to fix broken images/scripts
            if tag.has_attr('src'):
                src = tag['src']
                if src.startswith('http'):
                    # Encrypt the resource URL
                    # Note: This is synchronous, but encrypt_payload is sync so it's fine.
                    # We need to make sure the client can handle this.
                    # Since we can't easily inject the payload into a GET param without exposing it,
                    # we will use a new endpoint /api/proxy/resource?payload=...
                    payload = self.encrypt_payload(src, exp_seconds=300)
                    tag['src'] = f"/api/proxy/resource?payload={payload}"
            
            # Handle link href (CSS)
            if tag.name == 'link' and tag.has_attr('href'):
                href = tag['href']
                if href.startswith('http'):
                    payload = self.encrypt_payload(href, exp_seconds=300)
                    tag['href'] = f"/api/proxy/resource?payload={payload}"

        return str(soup)

    async def stream_response(self, response: httpx.Response, client_ip: str = "unknown"):
        total_bytes = 0
        async for chunk in response.aiter_bytes(CHUNK_SIZE):
            size = len(chunk)
            total_bytes += size
            yield chunk
            
            # Rate limiting
            expected_time = size / MAX_SPEED_BPS
            await asyncio.sleep(expected_time)
        
        # Log bandwidth after stream finishes (or periodically if needed, but this is simpler)
        db_utils.log_bandwidth(client_ip, total_bytes, 0, "proxy")

proxy_service = ProxyService()
