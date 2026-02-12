const express = require('express');
const Unblocker = require('unblocker');
const { Transform } = require('stream');
const ytdl = require('ytdl-core');
const http = require('http');

const app = express();
const PORT = process.env.PORT || 8080;
const MAIN_APP_URL = process.env.MAIN_APP_URL || 'http://localhost:8000';
const THROTTLE_BPS = 1024 * 1024; // 1 MB/s

// --- Session Verification ---
const sessionCache = new Map(); // token -> { role: string, expiry: number }

function checkSession(token) {
    return new Promise((resolve) => {
        if (!token) return resolve(null);
        
        const cached = sessionCache.get(token);
        if (cached && cached.expiry > Date.now()) {
            return resolve(cached.role);
        }

        const req = http.get(`${MAIN_APP_URL}/api/internal/check_session?token=${token}`, (res) => {
            if (res.statusCode !== 200) {
                res.resume();
                resolve(null);
                return;
            }
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const json = JSON.parse(data);
                    // Cache for 60 seconds
                    sessionCache.set(token, { role: json.role, expiry: Date.now() + 60000 });
                    resolve(json.role);
                } catch(e) {
                    resolve(null);
                }
            });
        });
        req.on('error', () => {
            resolve(null);
        });
        req.end();
    });
}

// --- Throttle Stream ---
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

const unblocker = new Unblocker({
    prefix: '/proxy/',
    requestMiddleware: [
        // YouTube workaround
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
</head>
<body>
<div class="back"><a href="javascript:history.back()">← Back</a></div>
<video controls poster="/proxy/${thumb.url}" autoplay style="width: 100%">
${formats.map(format => `<source type="${format.mimeType.split(";").shift()}" src="/proxy/${format.url.replace(/&/g, "&amp;")}">`).join("\n")}
</video>
<div class="info">
<h1>${info.videoDetails.title}</h1>
<p>${info.videoDetails.description ? info.videoDetails.description.replace(/[\n]/g, "\n<br>") : ''}</p>
</div>
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
        async (data) => {
            // Bandwidth Throttling
            if (data.contentType && (
                data.contentType.startsWith('video/') || 
                data.contentType.startsWith('application/octet-stream')
            )) {
                try {
                    const userHeaders = data.clientRequest.headers;
                    const cookie = userHeaders.cookie || '';
                    const match = cookie.match(/ytdlp_auth=([^;]+)/);
                    const token = match ? match[1] : null;
                    
                    const role = await checkSession(token);
                    if (role !== 'admin') {
                        data.stream = data.stream.pipe(new ThrottleStream(THROTTLE_BPS));
                    }
                } catch(e) {
                     // Fallback to throttle on error
                     data.stream = data.stream.pipe(new ThrottleStream(THROTTLE_BPS));
                }
            }
        }
    ]
});

app.use(unblocker);

app.get('/', (req, res) => {
    res.send(`
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Secure Browser</title>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #202124; color: #e8eaed; }
        #nav-bar { height: 50px; background: #292a2d; display: flex; align-items: center; padding: 0 10px; border-bottom: 1px solid #3c4043; gap: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
        .nav-btn { background: transparent; border: none; color: #9aa0a6; border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: background 0.2s; font-size: 16px; }
        .nav-btn:hover { background: rgba(255,255,255,0.1); color: #e8eaed; }
        .nav-btn:active { background: rgba(255,255,255,0.2); }
        .url-bar-container { flex: 1; position: relative; display: flex; align-items: center; }
        #url-input { width: 100%; background: #202124; border: 1px solid #5f6368; border-radius: 20px; color: #e8eaed; padding: 6px 15px; font-size: 14px; outline: none; transition: border 0.2s, background 0.2s; height: 34px; box-sizing: border-box; }
        #url-input:focus { border-color: #8ab4f8; background: #292a2d; }
        #iframe-container { width: 100%; height: calc(100% - 50px); position: relative; }
        iframe { width: 100%; height: 100%; border: none; background: #fff; }
        .menu-dropdown { position: absolute; top: 45px; right: 10px; width: 250px; background: #292a2d; border: 1px solid #3c4043; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); display: none; z-index: 1000; overflow: hidden; padding: 5px 0; }
        .menu-item { padding: 8px 15px; cursor: pointer; display: flex; align-items: center; color: #e8eaed; font-size: 14px; }
        .menu-item:hover { background: rgba(255,255,255,0.1); }
        .loader { position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, #8ab4f8, #c58af9); animation: load 1s infinite linear; display: none; }
        @keyframes load { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
    </style>
</head>
<body>
    <div id="nav-bar">
        <button class="nav-btn" onclick="goBack()" title="戻る">←</button>
        <button class="nav-btn" onclick="goForward()" title="進む">→</button>
        <button class="nav-btn" onclick="reload()" title="再読み込み">↻</button>
        <button class="nav-btn" onclick="goHome()" title="ホーム">🏠</button>
        <div class="url-bar-container">
            <input type="text" id="url-input" placeholder="検索またはURLを入力" onkeydown="handleKey(event)">
        </div>
        <button class="nav-btn" onclick="toggleHistory()" title="履歴">🕒</button>
    </div>
    
    <div id="iframe-container">
        <div class="loader" id="loader"></div>
        <iframe id="content-frame" name="content-frame" sandbox="allow-forms allow-scripts allow-top-navigation allow-same-origin allow-popups allow-modals allow-downloads"></iframe>
    </div>

    <div id="history-menu" class="menu-dropdown"></div>

    <script>
        const frame = document.getElementById('content-frame');
        const input = document.getElementById('url-input');
        const loader = document.getElementById('loader');
        const historyMenu = document.getElementById('history-menu');
        const MAIN_APP = "${MAIN_APP_URL}";

        let historyLog = JSON.parse(localStorage.getItem('proxy_history') || '[]');

        function saveHistory(url) {
            if (!url) return;
            historyLog = historyLog.filter(u => u !== url);
            historyLog.unshift(url);
            if(historyLog.length > 50) historyLog.pop();
            localStorage.setItem('proxy_history', JSON.stringify(historyLog));
        }

        function loadHistoryUI() {
            historyMenu.innerHTML = '';
            if (historyLog.length === 0) {
                historyMenu.innerHTML = '<div class="menu-item" style="cursor:default; color:#888;">履歴なし</div>';
                return;
            }
            historyLog.forEach(url => {
                const div = document.createElement('div');
                div.className = 'menu-item';
                div.textContent = url;
                div.onclick = () => { navigate(url); toggleHistory(); };
                historyMenu.appendChild(div);
            });
            const clearBtn = document.createElement('div');
            clearBtn.className = 'menu-item';
            clearBtn.style.borderTop = '1px solid #444';
            clearBtn.style.color = '#f88';
            clearBtn.textContent = '履歴を消去';
            clearBtn.onclick = () => { localStorage.removeItem('proxy_history'); historyLog = []; loadHistoryUI(); };
            historyMenu.appendChild(clearBtn);
        }

        function toggleHistory() {
            historyMenu.style.display = (historyMenu.style.display === 'block') ? 'none' : 'block';
            if(historyMenu.style.display === 'block') loadHistoryUI();
        }
        
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#history-menu') && !e.target.closest('.nav-btn[title="履歴"]')) {
                historyMenu.style.display = 'none';
            }
        });

        function navigate(url) {
            if (!url) return;
            let target = url.trim();
            if (!target.match(/^https?:\\/\\//i)) {
                if (target.includes('.') && !target.includes(' ')) {
                    target = 'https://' + target;
                } else {
                    target = 'https://www.google.com/search?q=' + encodeURIComponent(target);
                }
            }
            saveHistory(target);
            loader.style.display = 'block';
            input.value = target;
            frame.src = '/proxy/' + target;
        }

        function handleKey(e) {
            if (e.key === 'Enter') navigate(input.value);
        }

        function goBack() { frame.contentWindow.history.back(); }
        function goForward() { frame.contentWindow.history.forward(); }
        function reload() { frame.contentWindow.location.reload(); }
        function goHome() { window.location.href = MAIN_APP; }

        frame.onload = () => {
            loader.style.display = 'none';
            try {
                const href = frame.contentWindow.location.href;
                if (href.includes('/proxy/')) {
                    let clean = href;
                    const idx = href.indexOf('/proxy/');
                    if (idx !== -1) clean = href.substring(idx + 7);
                    input.value = clean;
                    saveHistory(clean);
                }
            } catch (e) {}
        };

        const params = new URLSearchParams(window.location.search);
        if (params.get('url')) {
            navigate(params.get('url'));
        } else {
            navigate('https://www.google.com');
        }
    </script>
</body>
</html>
    `);
});

app.listen(PORT, () => {
    console.log(`Node Proxy running on port ${PORT}`);
});
