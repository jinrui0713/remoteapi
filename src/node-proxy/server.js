const express = require('express');
const Unblocker = require('unblocker');
const { Transform } = require('stream');
const ytdl = require('@distube/ytdl-core');
const http = require('http');
const querystring = require('querystring');

const app = express();
const PORT = process.env.PORT || 8080;
const MAIN_APP_URL = process.env.MAIN_APP_URL || 'http://localhost:8000';
const THROTTLE_BPS = 1024 * 1024; // 1 MB/s

// --- Session Verification ---
const sessionCache = new Map();

function checkSession(token) {
    return new Promise((resolve) => {
        if (!token) return resolve(null);
        const cached = sessionCache.get(token);
        if (cached && cached.expiry > Date.now()) return resolve(cached.role);

        const req = http.get(`${MAIN_APP_URL}/api/internal/check_session?token=${token}`, (res) => {
            if (res.statusCode !== 200) { res.resume(); resolve(null); return; }
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const json = JSON.parse(data);
                    sessionCache.set(token, { role: json.role, expiry: Date.now() + 60000 });
                    resolve(json.role);
                } catch(e) { resolve(null); }
            });
        });
        req.on('error', () => resolve(null));
        req.end();
    });
}

// --- Video/Throttle Stream ---
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
        const delay = (timeSinceLast < expectedTimeMs) ? (expectedTimeMs - timeSinceLast) : 0;
        
        if (delay > 0) {
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

// --- Title Spoofing Script ---
// Reads from the top frame's localStorage (if same-origin) to get the custom title
const TITLE_SPOOF_SCRIPT = `
<script>
(function(){
    var targetTitle = "東進学力 POS";
    try { 
        if(window.top && window.top.localStorage) {
            var stored = window.top.localStorage.getItem('spoof_title');
            if(stored) targetTitle = stored;
        }
    } catch(e){}

    function spoof(){ if(document.title !== targetTitle) document.title = targetTitle; }
    setInterval(spoof, 500);
    spoof();
    const observer = new MutationObserver(spoof);
    if(document.body) observer.observe(document.querySelector('title') || document.body, { subtree: true, characterData: true, childList: true });
})();
</script>
`;

const unblocker = new Unblocker({
    prefix: '/proxy/',
    requestMiddleware: [
        async (data) => {
            if (ytdl.validateURL(data.url)) {
                try {
                    const info = await ytdl.getInfo(data.url);
                    const res = data.clientResponse;
                    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
                    
                    // Filter formats: prefer audio+video, fallback to any video
                    let formats = ytdl.filterFormats(info.formats, "audioandvideo");
                    if (!formats || formats.length === 0) {
                        formats = ytdl.filterFormats(info.formats, f => f.hasVideo);
                    }
                    
                    const thumb = info.videoDetails.thumbnails.pop() || { url: '' };
                    
                    res.end(`
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video Player</title>
<style>
body { background: #000; color: #fff; margin:0; display:flex; flex-direction:column; align-items:center; min-height:100vh; font-family: sans-serif; }
video { max-width: 100%; max-height: 80vh; background: #000; }
.info { padding: 20px; max-width: 800px; width: 100%; }
h1 { font-size: 1.2rem; margin-bottom: 10px; }
p { font-size: 0.9rem; white-space: pre-wrap; color: #ccc; }
.back { padding: 10px; width: 100%; background: #222; margin-bottom: 20px; }
.back a { color: #fff; text-decoration: none; margin-left: 20px; }
</style>
${TITLE_SPOOF_SCRIPT}
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
                    return true;
                } catch (err) {
                    data.clientResponse.writeHead(500, { "content-type": "text/html; charset=utf-8" });
                    data.clientResponse.end(`<h1>YouTube Error</h1><pre>${err.stack}</pre>`);
                    return true;
                }
            }
        }
    ],
    responseMiddleware: [
        async (data) => {
            // Strip CSP
            if (data.headers) {
                delete data.headers['content-security-policy'];
                delete data.headers['content-security-policy-report-only'];
                delete data.headers['x-webkit-csp'];
                delete data.headers['x-content-security-policy'];
            }

            // Inject Title Spoofing
            if (data.contentType && data.contentType.includes('text/html')) {
                const decoder = new (require('string_decoder').StringDecoder)('utf8');
                let buffer = '';
                const injector = new Transform({
                    transform(chunk, encoding, callback) {
                        buffer += decoder.write(chunk);
                        if (buffer.includes('</head>')) {
                            const parts = buffer.split('</head>');
                            // Inject before closing head
                            this.push(parts[0] + TITLE_SPOOF_SCRIPT + '</head>');
                            buffer = parts[1];
                        } 
                         // Also check body if head is missing or split
                        if (buffer.length > 1024*64) {
                            this.push(buffer.slice(0, -100));
                            buffer = buffer.slice(-100);
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

            // Throttling
            if (data.contentType && (
                data.contentType.startsWith('video/') || 
                data.contentType.startsWith('application/octet-stream')
            )) {
                try {
                    const cookie = data.clientRequest.headers.cookie || '';
                    const match = cookie.match(/ytdlp_auth=([^;]+)/);
                    const token = match ? match[1] : null;
                    const role = await checkSession(token);
                    if (role !== 'admin') {
                        data.stream = data.stream.pipe(new ThrottleStream(THROTTLE_BPS));
                    }
                } catch(e) {
                     data.stream = data.stream.pipe(new ThrottleStream(THROTTLE_BPS));
                }
            }
        }
    ]
});

// Middleware for parsing POST body for proxy gateway
app.use((req, res, next) => {
    if (req.method === 'POST' && req.path === '/proxy_gateway') {
        let body = '';
        req.on('data', chunk => { body += chunk.toString(); });
        req.on('end', () => {
            req.body = querystring.parse(body);
            next();
        });
    } else {
        next();
    }
});

// Gateway to hide parsed URL from initial history
app.post('/proxy_gateway', (req, res, next) => {
    let target = req.body.url;
    if (!target) return res.redirect('/');
    
    // Normalize logic
    if (!target.startsWith('http')) {
         // Naive check
         if (!target.includes('://')) target = 'https://' + target;
    }

    // Trick unblocker: fake the URL and method logic
    req.url = '/proxy/' + target;
    req.method = 'GET'; // Convert POST navigation to GET proxy request
    
    next();
});

app.use(unblocker);

app.get('/', (req, res) => {
    res.send(`
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>東進学力 POS</title>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; font-family: "Hiragino Kaku Gothic ProN", Meiryo, sans-serif; background: #f0f2f5; color: #333; }
        #nav-bar { height: 50px; background: #fff; display: flex; align-items: center; padding: 0 15px; border-bottom: 1px solid #ddd; gap: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
        .nav-btn { background: transparent; border: none; color: #5f6368; border-radius: 4px; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: 0.2s; font-size: 18px; }
        .nav-btn:hover { background: #f0f0f0; color: #202124; }
        #url-input { flex: 1; background: #f1f3f4; border: 1px solid transparent; border-radius: 20px; padding: 8px 16px; font-size: 14px; outline: none; transition: 0.2s; }
        #url-input:focus { background: #fff; border-color: #1a73e8; box-shadow: 0 1px 2px rgba(0,0,0,0.1); }
        #iframe-container { width: 100%; height: calc(100% - 50px); position: relative; background: #fff; }
        iframe { width: 100%; height: 100%; border: none; }
        .menu-dropdown { position: absolute; top: 55px; right: 10px; width: 260px; background: #fff; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); display: none; z-index: 2000; max-height: 400px; overflow-y: auto; }
        .menu-item { padding: 10px 15px; cursor: pointer; border-bottom: 1px solid #eee; font-size: 13px; color: #333; }
        .menu-item:hover { background: #f8f9fa; }
    </style>
</head>
<body>
    <div id="nav-bar">
        <button class="nav-btn" onclick="goBack()" title="戻る">←</button>
        <button class="nav-btn" onclick="goForward()" title="進む">→</button>
        <button class="nav-btn" onclick="reload()" title="再読み込み">↻</button>
        <button class="nav-btn" onclick="goHome()" title="ホーム">🏠</button>
        
        <!-- Hidden Form for POST Navigation -->
        <form id="proxy-form" method="POST" action="/proxy_gateway" target="content-frame" style="display:none;">
            <input type="hidden" name="url" id="proxy-input-hidden">
        </form>

        <input type="text" id="url-input" placeholder="検索またはURLを入力 (Enterで移動)" onkeydown="handleKey(event)">
        
        <button class="nav-btn" onclick="openSettings()" title="タブ名などの設定">⚙️</button>
        <button class="nav-btn" onclick="toggleHistory()" title="履歴">🕒</button>
    </div>
    
    <div id="iframe-container">
        <iframe id="content-frame" name="content-frame" sandbox="allow-forms allow-scripts allow-top-navigation allow-same-origin allow-popups allow-modals allow-downloads"></iframe>
    </div>

    <div id="history-menu" class="menu-dropdown"></div>

    <script>
        const frame = document.getElementById('content-frame');
        const input = document.getElementById('url-input');
        const proxyForm = document.getElementById('proxy-form');
        const proxyInputHidden = document.getElementById('proxy-input-hidden');
        const historyMenu = document.getElementById('history-menu');
        const MAIN_APP = "${MAIN_APP_URL}";

        let historyLog = JSON.parse(localStorage.getItem('proxy_history') || '[]');

        function initTitle() {
            var t = localStorage.getItem('spoof_title');
            if(t) document.title = t;
        }
        initTitle();

        function openSettings() {
            var current = localStorage.getItem('spoof_title') || document.title;
            var newVal = prompt("プロキシ中のタブタイトルを入力してください（空白でデフォルト）:", current === "東進学力 POS" ? "" : current);
            if(newVal !== null) {
                if(!newVal.trim()) {
                    localStorage.removeItem('spoof_title');
                    document.title = "東進学力 POS";
                } else {
                    localStorage.setItem('spoof_title', newVal);
                    document.title = newVal;
                }
                // Reload iframe so injected script picks up new title
                if(frame.contentWindow) try { frame.contentWindow.location.reload(); } catch(e){}
            }
        }

        function saveHistory(url) {
            if (!url) return;
            historyLog = historyLog.filter(u => u !== url);
            historyLog.unshift(url);
            if(historyLog.length > 50) historyLog.pop();
            localStorage.setItem('proxy_history', JSON.stringify(historyLog));
        }

        function toggleHistory() {
            if (historyMenu.style.display === 'block') {
                historyMenu.style.display = 'none';
                return;
            }
            historyMenu.innerHTML = '';
            if (historyLog.length === 0) {
                historyMenu.innerHTML = '<div class="menu-item" style="color:#999">履歴なし</div>';
            } else {
                historyLog.forEach(url => {
                    const item = document.createElement('div');
                    item.className = 'menu-item';
                    item.textContent = url;
                    item.onclick = () => { navigate(url); toggleHistory(); };
                    historyMenu.appendChild(item);
                });
                const clear = document.createElement('div');
                clear.className = 'menu-item';
                clear.textContent = '履歴を消去';
                clear.style.color = 'red';
                clear.onclick = () => { localStorage.removeItem('proxy_history'); historyLog = []; toggleHistory(); };
                historyMenu.appendChild(clear);
            }
            historyMenu.style.display = 'block';
        }

        function navigate(url) {
            if (!url) return;
            let target = url.trim();
            if (!target.match(/^https?:\\/\\//i)) {
                 if (!target.includes(' ') && target.includes('.')) {
                     target = 'https://' + target;
                 } else {
                     target = 'https://www.google.com/search?q=' + encodeURIComponent(target);
                 }
            }
            
            saveHistory(target);
            input.value = target; 
            
            // POST Submit to hide from URL history
            proxyInputHidden.value = target;
            proxyForm.submit();
        }

        function handleKey(e) {
            if (e.key === 'Enter') navigate(input.value);
        }

        function goBack() { frame.contentWindow.history.back(); }
        function goForward() { frame.contentWindow.history.forward(); }
        function reload() { frame.contentWindow.location.reload(); }
        function goHome() { window.location.href = MAIN_APP; }

        // Initial Load
        const params = new URLSearchParams(window.location.search);
        if (params.get('url')) {
            navigate(params.get('url'));
            window.history.replaceState(null, '', '/');
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
