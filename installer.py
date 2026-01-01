import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sys
import os
import shutil
import subprocess
import ctypes
import winreg
import time

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)

class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YtDlp Server Installer")
        self.geometry("600x450")
        self.resizable(False, False)
        
        # Variables
        self.install_dir = tk.StringVar(value=os.path.join(os.environ['LOCALAPPDATA'], 'YtDlpApiServer'))
        self.port = tk.StringVar(value="8000")
        self.role = tk.StringVar(value="host") # host or server
        self.status_msg = tk.StringVar(value="Ready to install.")
        
        self.create_widgets()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        lbl_title = ttk.Label(main_frame, text="YtDlp Server Setup", font=("Segoe UI", 16, "bold"))
        lbl_title.pack(pady=(0, 20))
        
        # Role Selection
        lbl_role = ttk.Label(main_frame, text="Select Installation Role:", font=("Segoe UI", 10, "bold"))
        lbl_role.pack(anchor=tk.W)
        
        frame_role = ttk.Frame(main_frame)
        frame_role.pack(fill=tk.X, pady=5)
        
        rb_host = ttk.Radiobutton(frame_role, text="Host / Manager (Recommended)", variable=self.role, value="host")
        rb_host.pack(anchor=tk.W)
        ttk.Label(frame_role, text="   - Installs Server + Desktop Shortcut + Opens Browser", foreground="gray").pack(anchor=tk.W)
        
        rb_server = ttk.Radiobutton(frame_role, text="Server Node (Worker)", variable=self.role, value="server")
        rb_server.pack(anchor=tk.W, pady=(10, 0))
        ttk.Label(frame_role, text="   - Installs Background Service Only (Headless)", foreground="gray").pack(anchor=tk.W)
        
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)
        
        # Configuration
        lbl_config = ttk.Label(main_frame, text="Configuration:", font=("Segoe UI", 10, "bold"))
        lbl_config.pack(anchor=tk.W)
        
        # Install Path
        frame_path = ttk.Frame(main_frame)
        frame_path.pack(fill=tk.X, pady=5)
        ttk.Label(frame_path, text="Install Path:").pack(side=tk.LEFT)
        entry_path = ttk.Entry(frame_path, textvariable=self.install_dir)
        entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        btn_browse = ttk.Button(frame_path, text="Browse", command=self.browse_dir)
        btn_browse.pack(side=tk.LEFT)
        
        # Port
        frame_port = ttk.Frame(main_frame)
        frame_port.pack(fill=tk.X, pady=5)
        ttk.Label(frame_port, text="Server Port:").pack(side=tk.LEFT)
        entry_port = ttk.Entry(frame_port, textvariable=self.port, width=10)
        entry_port.pack(side=tk.LEFT, padx=5)
        
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=15)
        
        # Status & Buttons
        lbl_status = ttk.Label(main_frame, textvariable=self.status_msg, foreground="blue")
        lbl_status.pack(pady=(0, 10))
        
        # Make the button larger and easier to click
        style = ttk.Style()
        style.configure('Big.TButton', font=('Segoe UI', 12, 'bold'))
        
        btn_install = ttk.Button(main_frame, text="INSTALL NOW", command=self.start_install, style='Big.TButton')
        btn_install.pack(fill=tk.X, ipady=10, pady=10)

    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.install_dir.get())
        if d:
            self.install_dir.set(d)

    def start_install(self):
        # Admin check is now done at startup
        self.status_msg.set("Installing...")
        self.update()
        
        try:
            self.perform_install()
            messagebox.showinfo("Success", "Installation Complete!")
            
            if self.role.get() == "host":
                # Open browser
                import webbrowser
                webbrowser.open(f"http://localhost:{self.port.get()}")
            
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"Installation failed:\n{str(e)}")
            self.status_msg.set("Installation failed.")

    def perform_install(self):
        target_dir = self.install_dir.get()
        port = self.port.get()
        
        # 1. Create Directory
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
            
        # 2. Extract Files
        # In PyInstaller, bundled files are in sys._MEIPASS
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        # Files to copy
        files_to_copy = ['YtDlpApiServer.exe', 'ffmpeg.exe', 'ffprobe.exe']
        
        for f in files_to_copy:
            src = os.path.join(base_path, f)
            dst = os.path.join(target_dir, f)
            if os.path.exists(src):
                shutil.copy2(src, dst)
            else:
                # If running from source (not bundled), might be in different places
                # Try looking in release folder or current folder
                if os.path.exists(f):
                    shutil.copy2(f, dst)
                elif os.path.exists(os.path.join('release', f)):
                    shutil.copy2(os.path.join('release', f), dst)
        
        # Copy static folder
        src_static = os.path.join(base_path, 'static')
        dst_static = os.path.join(target_dir, 'static')
        if os.path.exists(src_static):
            if os.path.exists(dst_static):
                shutil.rmtree(dst_static)
            shutil.copytree(src_static, dst_static)
        elif os.path.exists('static'): # Source mode
             if os.path.exists(dst_static):
                shutil.rmtree(dst_static)
             shutil.copytree('static', dst_static)

        # 3. Register Scheduled Task
        exe_path = os.path.join(target_dir, "YtDlpApiServer.exe")
        task_name = "YtDlpApiServer"
        
        # Unregister old task
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True)
        
        # Register new task
        # Action: exe --port X
        action = f'"{exe_path}"'
        args = f'--port {port}'
        
        # Powershell command to register task
        ps_script = f"""
        $Action = New-ScheduledTaskAction -Execute '{exe_path}' -Argument '{args}' -WorkingDirectory '{target_dir}'
        $Trigger = New-ScheduledTaskTrigger -AtStartup
        $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden
        Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName '{task_name}' -Force
        """
        
        subprocess.run(["powershell", "-Command", ps_script], check=True)
        
        # 4. Firewall Rule
        # Always open port, just in case
        ps_firewall = f"""
        Remove-NetFirewallRule -DisplayName '{task_name}' -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName '{task_name}' -Direction Inbound -LocalPort {port} -Protocol TCP -Action Allow -Profile Any
        """
        subprocess.run(["powershell", "-Command", ps_firewall], check=True)
        
        # 5. Start Task Immediately
        subprocess.run(["schtasks", "/Run", "/TN", task_name], check=True)
        
        # 6. Create Desktop Shortcut (Host Mode)
        if self.role.get() == "host":
            desktop = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
            shortcut_path = os.path.join(desktop, "YtDlp Manager.url")
            with open(shortcut_path, 'w') as f:
                f.write(f"[InternetShortcut]\nURL=http://localhost:{port}\nIconIndex=0\nIconFile={exe_path}\n")

if __name__ == "__main__":
    if not is_admin():
        # Re-run the program with admin rights
        run_as_admin()
        sys.exit()
        
    app = InstallerApp()
    app.mainloop()
