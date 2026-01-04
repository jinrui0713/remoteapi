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
    
    conn.commit()
    conn.close()

def update_client_info(client_id: str, ip: str, info: Dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO clients 
                     (client_id, ip, user_agent, screen_res, window_size, color_depth, theme, orientation, last_seen)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (client_id, ip, info.get('ua'), info.get('screen'), info.get('window'), 
                   info.get('depth'), info.get('theme'), info.get('orientation'), time.time()))
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
