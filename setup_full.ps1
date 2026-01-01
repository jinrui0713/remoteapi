# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    Write-Host "Please right-click and select 'Run as administrator'." -ForegroundColor Yellow
    Pause
    exit
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot
Set-Location $ScriptPath

# Define AppData bin directory
$AppDataDir = Join-Path $env:LOCALAPPDATA "YtDlpApiServer"
$BinDir = Join-Path $AppDataDir "bin"

# Create directories if they don't exist
if (-not (Test-Path $BinDir)) {
    Write-Host "Creating AppData directory: $BinDir"
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

# Enable TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Robust Download Function ---
function Download-File {
    param (
        [string]$Url,
        [string]$OutputFile
    )

    if (Test-Path $OutputFile) {
        Write-Host "File already exists, skipping download: $(Split-Path $OutputFile -Leaf)" -ForegroundColor Green
        return
    }

    $UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    $Downloaded = $false

    Write-Host "Downloading: $Url"

    # Method 1: Standard Invoke-WebRequest
    if (-not $Downloaded) {
        try {
            Write-Host "  [Attempt 1] Standard download..." -NoNewline
            Invoke-WebRequest -Uri $Url -OutFile $OutputFile -UserAgent $UserAgent -ErrorAction Stop
            Write-Host " Success" -ForegroundColor Green
            $Downloaded = $true
        } catch {
            Write-Host " Failed ($($_))" -ForegroundColor Yellow
        }
    }

    # Method 2: Invoke-WebRequest with Proxy Bypass (Direct Connection)
    if (-not $Downloaded) {
        try {
            Write-Host "  [Attempt 2] Bypassing proxy..." -NoNewline
            $OldProxy = [System.Net.WebRequest]::DefaultWebProxy
            [System.Net.WebRequest]::DefaultWebProxy = $null
            
            Invoke-WebRequest -Uri $Url -OutFile $OutputFile -UserAgent $UserAgent -ErrorAction Stop
            
            [System.Net.WebRequest]::DefaultWebProxy = $OldProxy
            Write-Host " Success" -ForegroundColor Green
            $Downloaded = $true
        } catch {
            Write-Host " Failed ($($_))" -ForegroundColor Yellow
        }
    }

    # Method 3: .NET WebClient
    if (-not $Downloaded) {
        try {
            Write-Host "  [Attempt 3] .NET WebClient..." -NoNewline
            $WebClient = New-Object System.Net.WebClient
            $WebClient.Headers.Add("User-Agent", $UserAgent)
            $WebClient.DownloadFile($Url, $OutputFile)
            Write-Host " Success" -ForegroundColor Green
            $Downloaded = $true
        } catch {
            Write-Host " Failed ($($_))" -ForegroundColor Yellow
        }
    }

    if (-not $Downloaded) {
        Write-Error "All download methods failed for $Url"
    }
}

# --- Python Environment Setup ---
Write-Host "`n=== Setting up Python Environment ===" -ForegroundColor Cyan

# Check for Python
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Warning "Python is not installed or not in PATH."
    Write-Host "Please install Python 3.8+ from https://www.python.org/downloads/"
    Write-Host "Make sure to check 'Add Python to PATH' during installation."
    Pause
    exit
}

# Create venv if not exists
if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv venv
}

# Activate venv and install requirements
Write-Host "Installing requirements..."
& ".\venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\venv\Scripts\python.exe" -m pip install -r requirements.txt

# --- Build Exe ---
Write-Host "`n=== Building Executable ===" -ForegroundColor Cyan
$ExeName = "YtDlpApiServer.exe"
$ExePath = Join-Path $AppDataDir $ExeName

# Stop existing process if running
$Running = Get-Process -Name "YtDlpApiServer" -ErrorAction SilentlyContinue
if ($Running) {
    Write-Host "Stopping existing YtDlpApiServer process..."
    Stop-Process -Name "YtDlpApiServer" -Force
    Start-Sleep -Seconds 2
}

# Always rebuild to ensure latest code is used
Write-Host "Building exe with PyInstaller..."
& ".\venv\Scripts\pyinstaller.exe" --onefile --name YtDlpApiServer --clean --distpath dist main.py

Write-Host "Deploying exe to AppData..."
Copy-Item -Path "dist\$ExeName" -Destination $ExePath -Force


# Deploy static files
$StaticSrc = Join-Path $ScriptPath "static"
$StaticDest = Join-Path $AppDataDir "static"
if (Test-Path $StaticSrc) {
    Write-Host "Deploying static files..."
    if (-not (Test-Path $StaticDest)) {
        New-Item -ItemType Directory -Path $StaticDest -Force | Out-Null
    }
    Copy-Item -Path "$StaticSrc\*" -Destination $StaticDest -Recurse -Force
}

# --- FFmpeg Setup ---
Write-Host "`n=== Setting up FFmpeg ===" -ForegroundColor Cyan
$FFmpegExe = Join-Path $BinDir "ffmpeg.exe"
$FFprobeExe = Join-Path $BinDir "ffprobe.exe"

if (-not (Test-Path $FFmpegExe) -or -not (Test-Path $FFprobeExe)) {
    $FFmpegZip = Join-Path $BinDir "ffmpeg.zip"
    Download-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $FFmpegZip
    
    Write-Host "Extracting FFmpeg..."
    Expand-Archive -Path $FFmpegZip -DestinationPath $BinDir -Force
    
    # Move binaries to bin root
    $ExtractedRoot = Get-ChildItem -Path $BinDir -Filter "ffmpeg-master-*" -Directory | Select-Object -First 1
    if ($ExtractedRoot) {
        $BinSource = Join-Path $ExtractedRoot.FullName "bin"
        Copy-Item -Path "$BinSource\ffmpeg.exe" -Destination $BinDir -Force
        Copy-Item -Path "$BinSource\ffprobe.exe" -Destination $BinDir -Force
        Remove-Item -Path $ExtractedRoot.FullName -Recurse -Force
    }
    Remove-Item -Path $FFmpegZip -Force
} else {
    Write-Host "FFmpeg already exists in bin folder." -ForegroundColor Green
}

# --- Cloudflared Setup ---
Write-Host "`n=== Setting up Cloudflare Tunnel ===" -ForegroundColor Cyan
$CloudflaredExe = Join-Path $BinDir "cloudflared.exe"
Download-File "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" $CloudflaredExe

# Check for token file
$TokenFile = Join-Path $ScriptPath "cloudflare_token.txt"
if (Test-Path $TokenFile) {
    $Token = Get-Content $TokenFile -Raw
    $Token = $Token.Trim()
    
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        Write-Host "Token found. Configuring Cloudflare Tunnel service..."
        
        # Stop and uninstall existing service if any
        try {
            & $CloudflaredExe service stop 2>$null
            & $CloudflaredExe service uninstall 2>$null
            Start-Sleep -Seconds 2
        } catch {}

        # Install and start service
        try {
            & $CloudflaredExe service install $Token
            & $CloudflaredExe service start
            Write-Host "Cloudflare Tunnel service installed and started." -ForegroundColor Green
        } catch {
            Write-Warning "Failed to install Cloudflare service: $_"
        }
    } else {
        Write-Warning "Token file is empty."
    }
} else {
    Write-Warning "cloudflare_token.txt not found. Skipping Tunnel setup."
}

# --- Server Auto-Start Setup ---
Write-Host "`n=== Setting up Server Auto-Start ===" -ForegroundColor Cyan

$TaskName = "YtDlpApiServer"
$ExePath = Join-Path $AppDataDir "YtDlpApiServer.exe"

if (-not (Test-Path $ExePath)) {
    Write-Error "Executable not found at $ExePath"
    exit
}

$Action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $AppDataDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "Server auto-start task registered." -ForegroundColor Green
} catch {
    Write-Error "Failed to register server task: $_"
}

# --- Firewall Setup ---
Write-Host "`n=== Setting up Firewall ===" -ForegroundColor Cyan
$FirewallRuleName = "YtDlpApiServer"
$Port = 8000

try {
    Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Any | Out-Null
    Write-Host "Firewall rule added for port $Port." -ForegroundColor Green
} catch {
    Write-Warning "Failed to set firewall rule: $_"
}

# --- Start Server Now ---
Write-Host "`n=== Starting Server Now ===" -ForegroundColor Cyan

# Check if already running
$Running = Get-Process -Name "YtDlpApiServer" -ErrorAction SilentlyContinue
if (-not $Running) {
    Write-Host "Starting server in background..."
    
    # Ensure VBScript uses correct working directory
    $VbsScript = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$AppDataDir"
WshShell.Run """$ExePath""", 0, False
"@
    $VbsFile = Join-Path $ScriptPath "start_server_hidden_temp.vbs"
    Set-Content -Path $VbsFile -Value $VbsScript
    Invoke-Item $VbsFile
    Start-Sleep -Seconds 1
    Remove-Item $VbsFile
    
    # Wait and check if port is listening
    Write-Host "Waiting for server to start..." -NoNewline
    $MaxRetries = 10
    $Started = $false
    for ($i = 0; $i -lt $MaxRetries; $i++) {
        Start-Sleep -Seconds 1
        $TcpConnection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($TcpConnection) {
            $Started = $true
            break
        }
        Write-Host "." -NoNewline
    }
    
    if ($Started) {
        Write-Host " Success!" -ForegroundColor Green
        Write-Host "Server is running on http://localhost:$Port"
    } else {
        Write-Host " Failed." -ForegroundColor Red
        Write-Warning "Server failed to start or is taking too long."
        Write-Warning "Checking server.log in AppData..."
        $LogFile = Join-Path $AppDataDir "server.log"
        if (Test-Path $LogFile) {
            Get-Content $LogFile -Tail 20
        } else {
            Write-Warning "server.log not found at $LogFile"
        }
        Write-Host "Trying to start visibly for debugging..."
        
        # Run directly in current console to see output
        try {
            # Explicitly set working directory for the process
            Push-Location $AppDataDir
            & $ExePath
            Pop-Location
        } catch {
            Write-Error "Failed to run exe: $_"
        }
    }

} else {
    Write-Host "Server is already running." -ForegroundColor Yellow
}

Write-Host "`n=== Setup Complete! ===" -ForegroundColor Cyan
Write-Host "1. Tools (FFmpeg, Cloudflared) are installed in: $BinDir"
Write-Host "2. Cloudflare Tunnel is running as a service."
Write-Host "3. API Server is running in background."
Write-Host "4. Both will start automatically on reboot."
Write-Host "`nPress any key to exit..."
Pause

