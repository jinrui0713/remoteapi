import sqlite3
import os
import sys

def find_db():
    # Try default location logic
    path = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'YtDlpApiServer', 'server.db')
    if os.path.exists(path):
        return path
    
    # Try current directory
    if os.path.exists('server.db'):
        return 'server.db'
        
    # Try relative path common in this project structure
    if os.path.exists('YtDlpApiServer/server.db'):
        return 'YtDlpApiServer/server.db'

    return None

def update_passwords():
    db_path = find_db()
    if not db_path:
        print("Database 'server.db' not found. If this is a new install, the server will create it with correct passwords on startup.")
        print("If you have an existing database, please place this script next to it or configure the path.")
        return

    print(f"Updating database at: {db_path}")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Update admin
    c.execute("UPDATE users SET password = ? WHERE username = ?", ("Shogo3170!", "admin"))
    if c.rowcount > 0:
        print("Updated admin password.")
    else:
        print("Admin user not found.")

    # Update user
    c.execute("UPDATE users SET password = ? WHERE username = ?", ("0713", "user"))
    if c.rowcount > 0:
        print("Updated user password.")
    else:
        print("Shared 'user' account not found.")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    update_passwords()
