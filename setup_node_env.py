import os
import sys
import zipfile
import urllib.request
import subprocess
import shutil

# Configuration
NODE_VERSION = "v20.11.0"
NODE_DIST = f"node-{NODE_VERSION}-win-x64"
NODE_URL = f"https://nodejs.org/dist/{NODE_VERSION}/{NODE_DIST}.zip"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_DIR = os.path.join(APP_DIR, "src", "node-proxy")
NODE_BIN_DIR = os.path.join(PROXY_DIR, "node_bin")

def download_node():
    if not os.path.exists(PROXY_DIR):
        os.makedirs(PROXY_DIR)
    
    if not os.path.exists(NODE_BIN_DIR):
        os.makedirs(NODE_BIN_DIR)
        
    node_exe = os.path.join(NODE_BIN_DIR, NODE_DIST, "node.exe")
    if os.path.exists(node_exe):
        print(f"Node.js found at {node_exe}")
        return os.path.join(NODE_BIN_DIR, NODE_DIST)

    print(f"Downloading Node.js {NODE_VERSION}...")
    zip_path = os.path.join(PROXY_DIR, "node.zip")
    try:
        urllib.request.urlretrieve(NODE_URL, zip_path)
    except Exception as e:
        print(f"Failed to download Node.js: {e}")
        sys.exit(1)

    print("Extracting Node.js...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(NODE_BIN_DIR)
    
    os.remove(zip_path)
    return os.path.join(NODE_BIN_DIR, NODE_DIST)

def create_server_files():
    # 1. package.json
    pkg_json = """{
  "name": "ytdlp-proxy",
  "version": "1.0.0",
  "description": "Node.js proxy with unblocker",
  "main": "server.js",
  "dependencies": {
    "express": "^4.18.2",
    "unblocker": "^2.1.1"
  }
}
"""
    with open(os.path.join(PROXY_DIR, "package.json"), "w", encoding="utf-8") as f:
        f.write(pkg_json)

    # 2. server.js
    server_js = r"""
const express = require('express');
const Unblocker = require('unblocker');
const { Transform } = require('stream');
const { StringDecoder } = require('string_decoder');

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
                        
                        if (buffer.includes('</body>')) {
                            const parts = buffer.split('</body>');
                            this.push(parts[0] + INJECTED_JS + '</body>');
                            buffer = parts[1];
                        }
                        
                        if (buffer.length > 1024 * 64) { 
                             const keep = 20; 
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
"""
    with open(os.path.join(PROXY_DIR, "server.js"), "w", encoding="utf-8") as f:
        f.write(server_js)

def setup_env_and_install():
    node_path = download_node()
    
    # Windows paths
    npm_bin = os.path.join(node_path, "npm.cmd")
    
    create_server_files()
    
    print("Installing Node.js dependencies...")
    env = os.environ.copy()
    env["PATH"] = f"{node_path};{env['PATH']}"
    
    try:
        subprocess.run([npm_bin, "install"], cwd=PROXY_DIR, check=True, env=env, shell=True)
        print("Dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install dependencies: {e}")

if __name__ == "__main__":
    setup_env_and_install()
    print("\nNode.js Proxy setup complete!")
