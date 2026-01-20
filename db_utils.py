import sqlite3
import time
import os
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'YtDlpApiServer', 'server.db')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Logs Table
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        ip TEXT,
        event_type TEXT,
        details TEXT
    )''')
    
    # Bandwidth Table (Daily/IP aggregation can be done in query, but raw logs might be heavy. 
    # Let's store per-request usage or aggregate.)
    # For simplicity, let's log bandwidth events in a separate table or just use logs?
    # Let's use a specific table for bandwidth to keep it clean.
    c.execute('''CREATE TABLE IF NOT EXISTS bandwidth (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL,
        ip TEXT,
        bytes_sent INTEGER,
        bytes_received INTEGER,
        type TEXT -- 'proxy', 'download', 'upload'
    )''')
    
    # Blocked IPs
    c.execute('''CREATE TABLE IF NOT EXISTS blocked_ips (
        ip TEXT PRIMARY KEY,
        reason TEXT,
        timestamp REAL
    )''')

    # Client Fingerprints
    c.execute('''CREATE TABLE IF NOT EXISTS clients (
        client_id TEXT PRIMARY KEY,
        ip TEXT,
        user_agent TEXT,
        screen_res TEXT,
        window_size TEXT,
        color_depth INTEGER,
        theme TEXT,
        orientation TEXT,
        last_seen REAL
    )''')

    # Users Table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT, -- 'admin', 'user', 'pending'
        nickname TEXT,
        ip TEXT,
        device_name TEXT, 
        user_agent TEXT,
        screen_res TEXT,
        created_at REAL,
        last_login REAL
    )''')
    
    # File Owners Table
    c.execute('''CREATE TABLE IF NOT EXISTS file_owners (
        filename TEXT PRIMARY KEY,
        username TEXT,
        created_at REAL
    )''')

    # Initialize Default Users if Empty
    try:
        c.execute("ALTER TABLE clients ADD COLUMN device_name TEXT")
    except:
        pass
    try:
        c.execute("ALTER TABLE clients ADD COLUMN username TEXT")
    except:
        pass

    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        # Defaults
        current_time = time.time()
        c.execute("INSERT INTO users (username, password, role, nickname, created_at) VALUES (?, ?, ?, ?, ?)",
                  ("admin", "Shogo3170!", "admin", "Administrator", current_time))
        c.execute("INSERT INTO users (username, password, role, nickname, created_at) VALUES (?, ?, ?, ?, ?)",
                  ("user", "0713", "user", "Standard User", current_time))
    else:
        # Enforce passwords for default accounts on startup
        c.execute("UPDATE users SET password = ? WHERE username = ?", ("Shogo3170!", "admin"))
        c.execute("UPDATE users SET password = ? WHERE username = ?", ("0713", "user"))

    conn.commit()
    conn.close()

def estimate_device_name(ua: str, screen: str) -> str:
    ua = ua.lower()
    device = "Unknown Device"
    
    if "iphone" in ua:
        device = "iPhone"
    elif "ipad" in ua:
        device = "iPad"
    elif "android" in ua:
        if "mobile" in ua:
            device = "Android Mobile"
        else:
            device = "Android Tablet"
    elif "windows" in ua:
        device = "Windows PC"
    elif "macintosh" in ua or "mac os" in ua:
        device = "Mac"
    elif "linux" in ua:
        device = "Linux PC"
    
    # Refine with screen resolution if available
    # e.g. 1920x1080 -> Desktop? this is weak heuristic but requested.
    return device

def register_user_request(nickname: str, password: str, ip: str, ua: str, screen: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Check if nickname or derived username exists?
        # For simplicity, we create a pending user. Username will be assigned or same as nickname?
        # Let's say username = nickname for now, but check uniqueness.
        
        # If username exists, return False
        c.execute("SELECT 1 FROM users WHERE username = ?", (nickname,))
        if c.fetchone():
            conn.close()
            return False
            
        device_name = estimate_device_name(ua, screen)
        
        c.execute('''INSERT INTO users 
                     (username, password, role, nickname, ip, device_name, user_agent, screen_res, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (nickname, password, 'pending', nickname, ip, device_name, ua, screen, time.time()))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"DB Error (Register): {e}")
        return False

def verify_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
    row = c.fetchone()
    conn.close()
    
    if row:
        # Update last login
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), row['id']))
            conn.commit()
            conn.close()
        except:
            pass
            
        if str(row['role']) == 'pending':
            return None
            
        return dict(row)
    return None

def authenticate_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role, id FROM users WHERE username = ? AND password = ?", (username, password))
    row = c.fetchone()
    conn.close()
    
    if row:
        # Update last login
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), row[1]))
            conn.commit()
            conn.close()
        except:
            pass
            
        if row[0] == 'pending':
            return None # Not approved yet
        return row[0] # Return role
    return None

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def approve_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET role = 'user' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

def update_user(user_id: int, password: str = None, role: str = None, username: str = None, nickname: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if password:
        c.execute("UPDATE users SET password = ? WHERE id = ?", (password, user_id))
    if role:
        c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    if username:
        c.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
    if nickname:
        c.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id))
    conn.commit()
    conn.close()

def update_user_password(username: str, password: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET password = ? WHERE username = ?", (password, username))
    conn.commit()
    conn.close()

def get_pending_users_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM users WHERE role = 'pending'")
    count = c.fetchone()[0]
    conn.close()
    return count

def delete_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    
def get_user_stats(user_id: int):
    # This is tricky because logs/bandwidth are by IP.
    # Users might change IP.
    # But for now, let's just get the user IP from user table and query logs?
    # Or we should have been logging 'user' in logs table.
    # Existing logs table only has IP.
    # Best effort: Get user's registration IP or last known IP.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT ip FROM users WHERE id = ?", (user_id,))
    user_row = c.fetchone()
    
    if not user_row:
        conn.close()
        return {}
        
    ip = user_row['ip']
    
    # Query stats for this IP
    # Downloads (last 30 days)
    month_ago = time.time() - (30 * 24 * 3600)
    c.execute("SELECT count(*) FROM logs WHERE ip = ? AND event_type = 'DOWNLOAD_START' AND timestamp > ?", (ip, month_ago))
    download_count = c.fetchone()[0]
    
    # Proxy Accesses
    c.execute("SELECT count(*) FROM logs WHERE ip = ? AND event_type = 'PROXY_ACCESS' AND timestamp > ?", (ip, month_ago))
    proxy_count = c.fetchone()[0]
    
    # Bandwidth
    c.execute("SELECT SUM(bytes_sent + bytes_received) FROM bandwidth WHERE ip = ?", (ip,))
    bw = c.fetchone()[0] or 0
    
    # Last login
    c.execute("SELECT last_login FROM users WHERE id = ?", (user_id,))
    last_login = c.fetchone()[0]
    
    conn.close()
    
    return {
        "downloads_30d": download_count,
        "proxy_30d": proxy_count,
        "bandwidth_total": bw,
        "last_login": last_login
    }

def update_client_info(client_id: str, ip: str, info: Dict, username: str = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO clients 
                     (client_id, ip, user_agent, screen_res, window_size, color_depth, theme, orientation, last_seen, device_name, username)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (client_id, ip, info.get('ua'), info.get('screen'), info.get('window'), 
                   info.get('depth'), info.get('theme'), info.get('orientation'), time.time(), info.get('device_name'), username))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error (Client Info): {e}")

def log_event(ip: str, event_type: str, details: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO logs (timestamp, ip, event_type, details) VALUES (?, ?, ?, ?)",
                  (time.time(), ip, event_type, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

def log_bandwidth(ip: str, sent: int, received: int, type: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO bandwidth (timestamp, ip, bytes_sent, bytes_received, type) VALUES (?, ?, ?, ?, ?)",
                  (time.time(), ip, sent, received, type))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

def block_ip(ip: str, reason: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO blocked_ips (ip, reason, timestamp) VALUES (?, ?, ?)",
              (ip, reason, time.time()))
    conn.commit()
    conn.close()

def unblock_ip(ip: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM blocked_ips WHERE ip = ?", (ip,))
    conn.commit()
    conn.close()

def get_blocked_ips() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM blocked_ips")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def is_ip_blocked(ip: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM blocked_ips WHERE ip = ?", (ip,))
    result = c.fetchone()
    conn.close()
    return result is not None

def get_logs(limit: int = 100) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Join with clients table to get UA and device info
    c.execute('''
        SELECT logs.*, clients.user_agent, clients.screen_res, clients.window_size, clients.theme 
        FROM logs 
        LEFT JOIN clients ON logs.ip = clients.ip 
        ORDER BY logs.timestamp DESC LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_clients() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM clients ORDER BY last_seen DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_bandwidth_stats() -> Dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Total
    c.execute("SELECT SUM(bytes_sent), SUM(bytes_received) FROM bandwidth")
    total_sent, total_recv = c.fetchone()
    
    # Per IP (Top 10)
    c.execute('''
        SELECT ip, SUM(bytes_sent + bytes_received) as total 
        FROM bandwidth 
        GROUP BY ip 
        ORDER BY total DESC 
        LIMIT 10
    ''')
    top_ips = [{"ip": row[0], "total": row[1]} for row in c.fetchall()]
    
    conn.close()
    return {
        "total_sent": total_sent or 0,
        "total_received": total_recv or 0,
        "top_ips": top_ips
    }

def add_file_owner(filename: str, username: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO file_owners (filename, username, created_at) VALUES (?, ?, ?)",
                  (filename, username, time.time()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error (Add File Owner): {e}")

def get_file_owners() -> Dict[str, str]:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT filename, username FROM file_owners")
        rows = c.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        print(f"DB Error (Get File Owners): {e}")
        return {}

def check_username_exists(username: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        row = c.fetchone()
        conn.close()
        return row is not None
    except:
        return False
