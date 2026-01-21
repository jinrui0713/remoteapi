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
# Persistent Key Loading
KEY_FILE = os.path.join(os.environ.get('LOCALAPPDATA', os.getcwd()), 'YtDlpApiServer', 'proxy.key')
if not os.path.exists(os.path.dirname(KEY_FILE)):
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)

if os.path.exists(KEY_FILE):
    with open(KEY_FILE, 'rb') as f:
        PROXY_KEY = f.read()
else:
    PROXY_KEY = secrets.token_bytes(32)
    with open(KEY_FILE, 'wb') as f:
        f.write(PROXY_KEY)

DEFAULT_MAX_SPEED_BPS = 1024 * 1024 # 1MB/s (Throttled speed)
DEFAULT_BURST_THRESHOLD_BPS = 1.5 * 1024 * 1024 # 1.5MB/s (Trigger threshold)
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

    def _update_stats(self, client_ip: str, chunk_size: int, limit_bps: int = None) -> bool:
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
            
        # If no limit or unlimited (0), don't throttle
        if limit_bps is None or limit_bps <= 0:
            return False

        # Update Window
        if now - stats["window_start"] > BURST_DURATION:
            # Calculate speed in previous window
            duration = now - stats["window_start"]
            speed = stats["bytes"] / duration
            
            # Reset window
            stats["window_start"] = now
            stats["bytes"] = 0
            
            # Use burst threshold relative to limit (e.g. 1.5x)
            burst_threshold = limit_bps * 1.5
            
            # Check if we exceeded threshold
            if speed > burst_threshold:
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
            # Always recreate the client on error to ensure fresh state
            try:
                await self.client.aclose()
            except:
                pass
            
            self.client = httpx.AsyncClient(
                verify=False, 
                follow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

    def rewrite_html(self, html_content: bytes, base_url: str) -> str:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove noscript tags to prevent "Enable JS" warnings
        for ns in soup.find_all('noscript'):
            ns.decompose()

        # Remove likely blocking headers in meta tags
        for meta in soup.find_all('meta'):
            if meta.get('http-equiv', '').lower() in ['content-security-policy', 'x-frame-options', 'permissions-policy']:
                meta.decompose()
            # Remove existing charset declarations to avoid conflicts with our UTF-8 output
            if meta.get('charset'):
                meta.decompose()
            if meta.get('http-equiv', '').lower() == 'content-type':
                meta.decompose()

        # Inject UTF-8 meta ensures browser uses UTF-8 even if headers are missing
        if soup.head:
            meta_utf8 = soup.new_tag('meta', charset='utf-8')
            soup.head.insert(0, meta_utf8)
        
        from urllib.parse import urljoin
        
        # --- 1. Title Spoofing ---
        if soup.title:
            soup.title.string = "東進学力POS"
        else:
            new_title = soup.new_tag('title')
            new_title.string = "東進学力POS"
            if soup.head:
                soup.head.append(new_title)
            else:
                if not soup.html: soup.append(soup.new_tag('html'))
                if not soup.html.head: soup.html.insert(0, soup.new_tag('head'))
                soup.html.head.append(new_title)

        # --- 2. Inject Control Bar & Universal Proxy Script ---
        
        control_bar_styles = """
        #proxy-control-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 50px;
            background: #222;
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 15px;
            z-index: 2147483647;
            font-family: sans-serif;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.5);
        }
        #proxy-control-bar button {
            background: #444;
            color: #fff;
            border: 1px solid #555;
            padding: 5px 12px;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 5px;
        }
        #proxy-control-bar button:hover { background: #555; }
        #proxy-control-bar input {
            background: #333;
            border: 1px solid #555;
            color: #fff;
            padding: 5px 10px;
            border-radius: 4px;
            width: 300px;
        }
        #proxy-content-spacer { height: 50px; }
        """
        
        style_tag = soup.new_tag('style')
        style_tag.string = control_bar_styles
        if soup.head: soup.head.append(style_tag)

        # Main Proxy Script
        script = soup.new_tag('script')
        script.string = f"""
        // Title Watcher (Force "東進学力POS")
        setInterval(() => {{
            if (document.title !== "東進学力POS") document.title = "東進学力POS";
        }}, 500);

        // Core Proxy Navigation Function
        async function proxyGo(url) {{
            try {{
                // Save to history before navigating
                saveToHistory(url);
                
                const res = await fetch('/api/proxy/encrypt', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{url: url}})
                }});
                const data = await res.json();
                
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/proxy';
                
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'payload';
                input.value = data.payload;
                
                form.appendChild(input);
                document.body.appendChild(form);
                form.submit();
            }} catch (e) {{
                alert('Navigation failed: ' + e);
            }}
        }}

        // History Management
        function saveToHistory(url) {{
            let history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
            // Don't duplicate top
            if (history.length === 0 || history[history.length-1] !== url) {{
                history.push(url);
                sessionStorage.setItem('proxy_history', JSON.stringify(history));
            }}
        }}

        function goBack() {{
            let history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
            if (history.length > 1) {{
                history.pop(); // Remove current
                const prev = history.pop(); // Get previous
                sessionStorage.setItem('proxy_history', JSON.stringify(history)); // Save state
                proxyGo(prev);
            }} else {{
                alert('No history');
            }}
        }}

        function goHome() {{
             window.location.href = '/';
        }}
        
        function handleSearch(e) {{
            if (e.key === 'Enter') {{
                e.preventDefault(); // Prevent duplicated submissions
                let val = e.target.value.trim();
                // Simple smart search logic
                if (!val.startsWith('http') && !val.includes('://')) {{
                    // If no space and has dot, treat as domain
                    if (!val.includes(' ') && val.includes('.')) {{
                        val = 'http://' + val;
                    }} else {{
                        // Otherwise search
                        val = 'https://www.google.com/search?q=' + encodeURIComponent(val);
                    }}
                }}
                proxyGo(val);
            }}
        }}

        // Dynamic Resource Rewriter & Auto-Refresh Interceptor
        document.addEventListener("DOMContentLoaded", function() {{
            // Construct Control Bar
            const bar = document.createElement('div');
            bar.id = 'proxy-control-bar';
            
            // Create elements using DOM API instead of innerHTML to attach events easier for history
            bar.style.cssText = `
                position: fixed; bottom: 0; left: 0; width: 100%; height: 50px;
                background: #222; color: #fff; display: flex; align-items: center;
                justify-content: space-between; padding: 0 10px; z-index: 2147483647;
                font-family: sans-serif; box-shadow: 0 -2px 10px rgba(0,0,0,0.5);
            `;

            const grp = document.createElement('div');
            grp.style.display = 'flex';
            grp.style.gap = '5px';

            const btnStyle = "background: #444; color: #fff; border: 1px solid #555; padding: 5px 10px; border-radius: 4px; cursor: pointer;";
            
            const backBtn = document.createElement('button');
            backBtn.innerText = '←';
            backBtn.style.cssText = btnStyle;
            backBtn.onclick = goBack;

            const homeBtn = document.createElement('button');
            homeBtn.innerText = '🏠';
            homeBtn.style.cssText = btnStyle;
            homeBtn.onclick = goHome;
            
            const refreshBtn = document.createElement('button');
            refreshBtn.innerText = '↻';
            refreshBtn.style.cssText = btnStyle;
            refreshBtn.onclick = () => window.location.reload();

            // History Dropdown Button
            const histBtn = document.createElement('button');
            histBtn.innerText = '🕒';
            histBtn.style.cssText = btnStyle;
            histBtn.style.position = 'relative';
            histBtn.onclick = (e) => {{
                e.stopPropagation();
                let drop = document.getElementById('proxy-hist-drop');
                if (drop) {{ drop.remove(); return; }}
                
                drop = document.createElement('div');
                drop.id = 'proxy-hist-drop';
                drop.style.cssText = "position:absolute; bottom:40px; left:0; background:#333; border:1px solid #555; min-width:200px; max-height:300px; overflow-y:auto; border-radius:4px; box-shadow:0 -5px 15px rgba(0,0,0,0.5);";
                
                const history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
                if (history.length === 0) {{
                    drop.innerHTML = '<div style="padding:10px;color:#aaa;font-size:12px;">No History</div>';
                }} else {{
                    // Show reversed unique
                    [...new Set(history)].reverse().forEach(url => {{
                        const row = document.createElement('div');
                        row.innerText = url;
                        row.style.cssText = "padding:8px; border-bottom:1px solid #444; font-size:12px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:300px;";
                        row.onmouseover = () => row.style.background = '#555';
                        row.onmouseout = () => row.style.background = 'transparent';
                        row.onclick = () => {{ proxyGo(url); drop.remove(); }};
                        drop.appendChild(row);
                    }});
                }}
                // Append to button so it follows position? No, append to body or bar.
                // Creating a relative wrapper might be better, but let's append to bar (absolute)
                // Bar is fixed, so absolute inside it works relative to bar if bar has position (it does).
                // Actually button has position relative, so lets append to button?
                // No, button has overflow:hidden usually? No.
                // Let's append to histBtn
                console.log('Appended history');
                histBtn.appendChild(drop);
                
                 document.addEventListener('click', function c(ev) {{
                    if(!drop.contains(ev.target) && ev.target !== histBtn) {{
                        drop.remove();
                        document.removeEventListener('click', c);
                    }}
                }});
            }};

            grp.appendChild(backBtn);
            grp.appendChild(homeBtn);
            grp.appendChild(refreshBtn);
            grp.appendChild(histBtn);

            const searchInput = document.createElement('input');
            searchInput.type = 'text';
            searchInput.placeholder = 'Search...';
            searchInput.style.cssText = "flex-grow:1; margin:0 10px; padding:5px; border-radius:4px; border:none;";
            searchInput.onkeydown = handleSearch;

            bar.appendChild(grp);
            bar.appendChild(searchInput);
            document.body.appendChild(bar);
            
            const spacer = document.createElement('div');
            spacer.id = 'proxy-content-spacer';
            document.body.appendChild(spacer);

            // Mutation Observer for dynamic content
            const observer = new MutationObserver((mutations) => {{
                mutations.forEach((mutation) => {{
                    mutation.addedNodes.forEach((node) => {{
                        if (node.nodeType === 1) {{ // Tiago
                            rewriteAttributes(node);
                        }}
                    }});
                }});
            }});
            observer.observe(document.body, {{ childList: true, subtree: true }});
        }});
        
        // Helper to rewrite generic attributes async
        async function rewriteAttributes(node) {{
            if (node.tagName === 'IMG' && node.src && !node.src.includes(window.location.host)) {{
                rewriteResource(node, 'src');
            }}
            if (node.tagName === 'SCRIPT' && node.src && !node.src.includes(window.location.host)) {{
                rewriteResource(node, 'src');
            }}
            if (node.tagName === 'LINK' && node.href && !node.href.includes(window.location.host)) {{
                rewriteResource(node, 'href');
            }}
            if (node.tagName === 'IFRAME' && node.src && !node.src.includes(window.location.host)) {{
                // Iframes need full proxy treatment ideally, or resource proxy if simple
                // Let's try resource first, but navigation inside iframe will break out
                rewriteResource(node, 'src');
            }}
        }}

        async function rewriteResource(node, attr) {{
            const originalUrl = node[attr];
            // Prevent double-rewrite
            if (originalUrl.includes('/api/proxy/resource')) return;
            
            try {{
                const res = await fetch('/api/proxy/encrypt', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{url: originalUrl}})
                }});
                const data = await res.json();
                node[attr] = `/api/proxy/resource?payload=${{data.payload}}`;
            }} catch (e) {{
                // Silent fail
            }}
        }}
        
        // Override standard window functions
        const originalOpen = window.open;
        window.open = function(url, target, features) {{
             proxyGo(url);
             // Return dummy object to prevent null reference errors
             return {{
                 focus: () => {{}},
                 close: () => {{}},
                 document: document,
                 location: {{ href: url }}
             }};
        }};
        
        // Simple location override (imperfect but catches some cases)
        // Note: Direct assignment to window.location.href is hard to trap 100% without proxy objects
        
        """;
        if soup.head:
            soup.head.append(script)
        else:
            soup.append(script)

        # Rewrite links and resources
        for tag in soup.find_all(['a', 'link', 'script', 'img', 'iframe', 'form']):
            # Remove integrity checks as we are proxying content
            if tag.has_attr('integrity'):
                del tag['integrity']

            # Handle href (Navigation)
            if tag.name == 'a' and tag.has_attr('href'):
                url = tag['href']
                # Join with base URL
                full_url = urljoin(base_url, url)
                
                # Check if it's a valid http/https link (not javascript:, mailto:, etc)
                if full_url.startswith(('http://', 'https://')):
                    # Check target="_blank"
                    if tag.has_attr('target'):
                         del tag['target'] 
                    
                    tag['href'] = '#'
                    tag['onclick'] = f"proxyGo('{full_url}'); return false;"
            
            # Handle src (Resources)
            if tag.has_attr('src'):
                src = tag['src']
                full_src = urljoin(base_url, src)
                if full_src.startswith(('http://', 'https://')):
                    payload = self.encrypt_payload(full_src, exp_seconds=300)
                    tag['src'] = f"/api/proxy/resource?payload={payload}"
            
            # Handle srcset (Responsive Images)
            if tag.has_attr('srcset'):
                try:
                    srcset_val = tag['srcset']
                    new_parts = []
                    for part in srcset_val.split(','):
                        part = part.strip()
                        if not part: continue
                        # Format: "url 1x" or just "url"
                        subparts = part.split(' ')
                        p_url = subparts[0]
                        full_p_url = urljoin(base_url, p_url)
                        if full_p_url.startswith(('http://', 'https://')):
                            payload = self.encrypt_payload(full_p_url, exp_seconds=300)
                            new_url = f"/api/proxy/resource?payload={payload}"
                            # Reconstruct
                            subparts[0] = new_url
                            new_parts.append(' '.join(subparts))
                        else:
                            new_parts.append(part)
                    tag['srcset'] = ', '.join(new_parts)
                except:
                    pass
            
            # Handle Inline Styles (background-image etc)
            if tag.has_attr('style'):
                style = tag['style']
                # regex to find url(...)
                import re
                def start_repl(match):
                    url_val = match.group(1).strip("'\"")
                    if url_val.startswith('data:'): return match.group(0)
                    full_val = urljoin(base_url, url_val)
                    if full_val.startswith(('http://', 'https://')):
                         payload = self.encrypt_payload(full_val, exp_seconds=300)
                         return f"url('/api/proxy/resource?payload={payload}')"
                    return match.group(0)

                new_style = re.sub(r'url\((.*?)\)', start_repl, style)
                tag['style'] = new_style

            # Handle link href (CSS, Favicons) - EXCLUDE if it was already handled as 'a' (unlikely for link tag)
            if tag.name == 'link' and tag.has_attr('href'):
                href = tag['href']
                full_href = urljoin(base_url, href)
                # Stylesheets and icons should be proxied as resources
                if full_href.startswith(('http://', 'https://')):
                    # Check if it is CSS
                    rel = tag.get('rel', [])
                    if 'stylesheet' in rel or href.endswith('.css'):
                         # CSS needs special handling ideally (rewrite inside), but for now resource proxy is step 1
                         # If we just proxy the css file, the relative links INSIDE the css will break unless the proxy rewrite endpoint handles CSS content type.
                         # Current implementation of /api/proxy/resource is a simple streamer. 
                         # TODO: CSS internally referenced URLs will break. 
                         pass
                    
                    payload = self.encrypt_payload(full_href, exp_seconds=300)
                    tag['href'] = f"/api/proxy/resource?payload={payload}"
            
            # Handle Form Actions
            if tag.name == 'form' and tag.has_attr('action'):
                action = tag['action']
                full_action = urljoin(base_url, action)
               
                tag['action'] = '#'
                # We override submission to use proxyGo for GET, and intercept POST
                # Note: This is a robust "Bypass" request.
                tag['onsubmit'] = f"""
                    event.preventDefault(); 
                    const formData = new FormData(this);
                    const params = new URLSearchParams(formData);
                    let url = '{full_action}';
                    if (this.method.toUpperCase() === 'GET') {{
                        url += '?' + params.toString();
                        proxyGo(url);
                    }} else {{
                         // For POST, we realistically need a server-side handler that accepts params
                         // Since we don't have a generic "Proxy POST" endpoint that takes arbitrary body,
                         // We downgrade to GET for compatibility or alert.
                         // But user asked to bypass EVERYTHING.
                         // Let's try to append params to URL even for POST (some sites accept mixed)
                         // OR, we can implement a more complex POST proxy later.
                         url += '?' + params.toString();
                         proxyGo(url);
                    }}
                """
        
        # Meta Refresh Handling
        for meta in soup.find_all('meta', attrs={"http-equiv": lambda x: x and x.lower() == 'refresh'}):
            if meta.has_attr('content'):
                content = meta['content']
                parts = content.split(';')
                if len(parts) > 1:
                    # url=...
                    delay = parts[0]
                    url_part = parts[1].strip()
                    if url_part.lower().startswith('url='):
                        target_url = url_part[4:]
                        full_target = urljoin(base_url, target_url)
                        meta.decompose()
                        
                        refresh_script = soup.new_tag('script')
                        refresh_script.string = f"setTimeout(function() {{ proxyGo('{full_target}'); }}, {delay} * 1000);"
                        if soup.head:
                            soup.head.append(refresh_script)

        return str(soup)

    async def stream_response(self, response: httpx.Response, client_ip: str = "unknown", limit_bps: int = None):
        try:
            total_bytes = 0
            
            # Check content type for HTML
            content_type = response.headers.get("content-type", "")
            base_url = str(response.url)
            
            if "text/html" in content_type:
                # Buffer HTML for rewriting
                content = await response.aread()
                total_bytes = len(content)
                
                # Use the centralized rewriter
                rewritten = self.rewrite_html(content, base_url)
                
                # Encode back to bytes
                # We assume UTF-8 for rewritten content usually
                try:
                    yield rewritten.encode('utf-8')
                except:
                    yield rewritten.encode('utf-8', errors='replace')

            elif "text/css" in content_type:
                # Buffer CSS for rewriting
                content = await response.aread()
                total_bytes = len(content)
                
                encoding = response.encoding or 'utf-8'
                try:
                    css_text = content.decode(encoding)
                except:
                    css_text = content.decode('utf-8', errors='replace')
                
                # Rewrite CSS URLs
                import re
                from urllib.parse import urljoin
                
                def css_url_repl(match):
                    url_val = match.group(1).strip("'\"")
                    if url_val.startswith('data:'): return match.group(0)
                    
                    full_val = urljoin(base_url, url_val)
                    if full_val.startswith(('http://', 'https://')):
                         payload = self.encrypt_payload(full_val, exp_seconds=300)
                         return f"url('/api/proxy/resource?payload={payload}')"
                    return match.group(0)

                rewritten_css = re.sub(r'url\((.*?)\)', css_url_repl, css_text)
                yield rewritten_css.encode('utf-8')
            
            else:
                # Streaming for non-HTML/CSS
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    size = len(chunk)
                    total_bytes += size
                    yield chunk
                    
                    if limit_bps and limit_bps > 0 and self._update_stats(client_ip, size, limit_bps):
                        expected_time = size / limit_bps
                        await asyncio.sleep(expected_time)
            
            # Log bandwidth
            db_utils.log_bandwidth(client_ip, total_bytes, 0, "proxy")
        except Exception as e:
            logging.error(f"Stream error: {e}")
        finally:
            await response.aclose()

proxy_service = ProxyService()
