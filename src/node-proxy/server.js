
const express = require('express');
const Unblocker = require('unblocker');
const { Transform } = require('stream');
const { StringDecoder } = require('string_decoder');
const ytdl = require('ytdl-core');

const app = express();
const PORT = process.env.PORT || 8080;
const MAIN_APP_URL = process.env.MAIN_APP_URL || 'http://localhost:8000';

// --- Configuration ---
const THROTTLE_BPS = 1024 * 1024; // 1 MB/s

// --- Throttling Stream ---
class ThrottleStream extends Transform {
    constructor(bps) {
        super();
        this.bps = bps;
        this.lastPush = Date.now();
    }
    _transform(chunk, encoding, callback) {
        const now = Date.now();
        const len = chunk.length;
        const expectedTimeMs = (len / this.bps) * 1000;
        const timeSinceLast = now - this.lastPush;
        
        if (timeSinceLast < expectedTimeMs) {
            const delay = expectedTimeMs - timeSinceLast;
            setTimeout(() => {
                this.push(chunk);
                this.lastPush = Date.now();
                callback();
            }, delay);
        } else {
            this.push(chunk);
            this.lastPush = Date.now();
            callback();
        }
    }
}

// --- Navigation Bar Injection ---
const INJECTED_CSS = `
<style>
#proxy-bar { position: fixed; bottom: 0; left: 0; width: 100%; height: 50px; background: #222; color: #fff; display: flex; align-items: center; justify-content: space-between; padding: 0 10px; z-index: 2147483647; font-family: sans-serif; box-shadow: 0 -2px 10px rgba(0,0,0,0.5); }
#proxy-bar button { background: #444; color: #fff; border: 1px solid #555; padding: 5px 10px; border-radius: 4px; cursor: pointer; margin-right: 5px; }
#proxy-bar button:hover { background: #555; }
#proxy-bar input { background: #333; border: 1px solid #555; color: #fff; padding: 5px 10px; border-radius: 4px; border: none; flex-grow: 1; margin: 0 10px; }
#proxy-spacer { height: 50px; }
</style>
`;

const INJECTED_JS = `
<script>
(function() {
    function saveToHistory(url) {
        try {
            let history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
            if (history.length === 0 || history[history.length-1] !== url) {
                history.push(url);
                sessionStorage.setItem('proxy_history', JSON.stringify(history));
            }
        } catch(e) {}
    }

    function goBack() {
        let history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
        if (history.length > 1) {
            history.pop(); 
            const prev = history.pop();
            sessionStorage.setItem('proxy_history', JSON.stringify(history));
            proxyGo(prev);
        } else {
            alert('No history');
        }
    }

    function handleSearch(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            let val = e.target.value.trim();
            if (!val.startsWith('http') && !val.includes('://')) {
                if (!val.includes(' ') && val.includes('.')) {
                    val = 'http://' + val;
                } else {
                    val = 'https://www.google.com/search?q=' + encodeURIComponent(val);
                }
            }
            proxyGo(val);
        }
    }

    window.proxyGo = function(url) {
        saveToHistory(url);
        window.location.href = '/proxy/' + url;
    };

    document.addEventListener("DOMContentLoaded", function() {
        if (document.getElementById('proxy-bar')) return;

        const bar = document.createElement('div');
        bar.id = 'proxy-bar';
        
        const grp = document.createElement('div');
        const backBtn = document.createElement('button');
        backBtn.innerText = '←';
        backBtn.onclick = goBack;
        
        const homeBtn = document.createElement('button');
        homeBtn.innerText = '🏠';
        // Go back to Python App Root
        homeBtn.onclick = () => window.location.href = "${MAIN_APP_URL}"; 
        
        const refreshBtn = document.createElement('button');
        refreshBtn.innerText = '↻';
        refreshBtn.onclick = () => window.location.reload();

         // History Dropdown
        const histBtn = document.createElement('button');
        histBtn.innerText = '🕒';
        histBtn.onclick = (e) => {
            e.stopPropagation();
            let drop = document.getElementById('proxy-hist-drop');
            if (drop) { drop.remove(); return; }
            drop = document.createElement('div');
            drop.id = 'proxy-hist-drop';
            drop.style.cssText = "position:absolute; bottom:50px; left:100px; background:#333; border:1px solid #555; min-width:200px; max-height:300px; overflow-y:auto; border-radius:4px;";
            
            const history = JSON.parse(sessionStorage.getItem('proxy_history') || '[]');
            [...new Set(history)].reverse().forEach(url => {
                const row = document.createElement('div');
                row.innerText = url;
                row.style.cssText = "padding:8px; cursor:pointer; color:#eee; border-bottom:1px solid #444; font-size:12px; white-space:nowrap; overflow:hidden;";
                row.onclick = () => { proxyGo(url); drop.remove(); };
                row.onmouseover = () => row.style.background = '#555';
                row.onmouseout = () => row.style.background = 'transparent';
                drop.appendChild(row);
            });
            document.body.appendChild(drop);
            document.addEventListener('click', () => drop.remove(), {once:true});
        };

        grp.appendChild(backBtn);
        grp.appendChild(homeBtn);
        grp.appendChild(refreshBtn);
        grp.appendChild(histBtn);

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Search or enter URL...';
        input.onkeydown = handleSearch;

        bar.appendChild(grp);
        bar.appendChild(input);
        document.body.appendChild(bar);

        const spacer = document.createElement('div');
        spacer.id = 'proxy-spacer';
        document.body.appendChild(spacer);
        
        const currentUrl = window.location.href.split('/proxy/')[1];
        if(currentUrl) saveToHistory(currentUrl);

        // Title Spoofing
        setInterval(() => {
             if (document.title !== "東進学力POS") document.title = "東進学力POS";
        }, 500);
    });
})();
</script>
`;

const unblocker = new Unblocker({
    prefix: '/proxy/',
    requestMiddleware: [
        (data) => {
            if (ytdl.validateURL(data.url)) {
                const res = data.clientResponse;
                res.writeHead(200, { "content-type": "text/html; charset=utf-8" });

                ytdl.getInfo(data.url).then((info) => {
                    const formats = ytdl.filterFormats(info.formats, "audioandvideo");
                    const thumb = info.videoDetails.thumbnails.pop() || { url: '' };

                    res.end(`
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${info.videoDetails.title}</title>
<style>
body { background: #000; color: #fff; margin:0; display:flex; flex-direction:column; align-items:center; min-height:100vh; font-family: sans-serif; }
video { max-width: 100%; max-height: 80vh; background: #000; }
.info { padding: 20px; max-width: 800px; width: 100%; }
h1 { font-size: 1.2rem; margin-bottom: 10px; }
p { font-size: 0.9rem; white-space: pre-wrap; color: #ccc; }
.back { padding: 10px; width: 100%; background: #222; margin-bottom: 20px; }
.back a { color: #fff; text-decoration: none; margin-left: 20px; }
</style>
${INJECTED_CSS}
</head>
<body>
<div class="back"><a href="${MAIN_APP_URL}/static/index.html">← Back to App</a></div>
<video controls poster="/proxy/${thumb.url}" autoplay style="width: 100%">
${formats.map(format => `<source type="${format.mimeType.split(";").shift()}" src="/proxy/${format.url.replace(/&/g, "&amp;")}">`).join("\n")}
</video>
<div class="info">
<h1>${info.videoDetails.title}</h1>
<p>${info.videoDetails.description ? info.videoDetails.description.replace(/[\n]/g, "\n<br>") : ''}</p>
</div>
${INJECTED_JS}
</body>
</html>
`);
                }).catch((err) => {
                    console.error(`Error getting info for ${data.url}`, err);
                    res.end(`Error retrieving video info: ${err.message}`);
                });
                return true; // Sent response
            }
        }
    ],
    responseMiddleware: [
        (data) => {
            // Bandwidth Throttling
            if (data.contentType && (
                data.contentType.startsWith('video/') || 
                data.contentType.startsWith('application/octet-stream')
            )) {
                data.stream = data.stream.pipe(new ThrottleStream(THROTTLE_BPS));
            }

            // HTML Injection
            if (data.contentType && data.contentType.includes('text/html')) {
                const decoder = new StringDecoder('utf8');
                let buffer = '';

                const injector = new Transform({
                    transform(chunk, encoding, callback) {
                        buffer += decoder.write(chunk);
                        
                        if (buffer.includes('</head>')) {
                            const parts = buffer.split('</head>');
                            this.push(parts[0] + INJECTED_CSS + '</head>');
                            buffer = parts[1];
                        }
                        
                        // INJECTED_JS definition needed if not defined globally in scope yet
                        // Wait, INJECTED_CSS is defined but INJECTED_JS ?
                        // Ah, INJECTED_JS is not defined in the original file I read!
                        // I see INJECTED_CSS above. Let's assume INJECTED_JS was added or should be added.
                        // Wait, I saw INJECTED_JS in my replacement code in previous step but didn't check for its definition.
                        // Let's scroll up in the file to see INJECTED_CSS definition.
                        
                        if (buffer.includes('</body>')) {
                            const parts = buffer.split('</body>');
                            this.push(parts[0] + INJECTED_JS + '</body>');
                            buffer = parts[1];
                        }

                        
                        if (buffer.length > 1024 * 64) { 
                             const keep = 20; 
                             // Ensure we don't slice in the middle of a multi-byte character if possible, 
                             // but buffer here is a string (decoder.write), so it's safe.
                             const flush = buffer.slice(0, buffer.length - keep);
                             buffer = buffer.slice(buffer.length - keep);
                             this.push(flush);
                        }
                        
                        callback();
                    },
                    flush(callback) {
                        if (buffer) this.push(buffer);
                        callback();
                    }
                });
                
                data.stream = data.stream.pipe(injector);
            }
        }
    ]
});

app.use(unblocker);

app.get('/', (req, res) => {
    res.redirect(MAIN_APP_URL);
});

app.listen(PORT, () => {
    console.log(`Node Proxy running on port ${PORT}`);
});
