# update_app.ps1

# Check for Administrator privileges and self-elevate
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "Not running as Administrator. Attempting to elevate..."
    try {
        Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
        exit
    } catch {
        Write-Error "Failed to elevate privileges. Please run as Administrator."
        Pause
        exit
    }
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot
Set-Location $ScriptPath

Start-Transcript -Path "update_log.txt" -Force

Write-Host "=== Starting Update Process ===" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date)"

# 1. Git Pull (Only if it's a git repo)
Write-Host "`n[1/4] Checking for updates..."
if (Test-Path ".git") {
    if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
        Write-Warning "Git is not installed. Skipping update check."
    } else {
        try {
            Write-Host "Checking GitHub for updates..."
            # Fix for "fatal: detected dubious ownership"
            git config --global --add safe.directory '*' 2>$null

            git fetch origin main
            $LocalHash = git rev-parse HEAD
            $RemoteHash = git rev-parse origin/main

            if ($LocalHash -eq $RemoteHash) {
                Write-Host "Already up to date." -ForegroundColor Green
            } else {
                Write-Host "New version found. Updating..." -ForegroundColor Yellow
                git reset --hard origin/main
                git pull origin main
            }
        } catch {
            Write-Warning "Git update failed. Continuing with local rebuild."
            Write-Warning $_
        }
    }
} else {
    Write-Host "Not a git repository. Attempting Binary Update..." -ForegroundColor Cyan
    
    $LatestReleaseUrl = "https://github.com/jinrui0713/remoteapi/releases/latest/download/Setup.exe"
    $InstallerPath = Join-Path $env:TEMP "YtDlpServer_Setup.exe"
    
    try {
        Write-Host "Downloading latest installer..."
        Invoke-WebRequest -Uri $LatestReleaseUrl -OutFile $InstallerPath
        
        Write-Host "Starting installer..."
        # Run installer and exit this script
        Start-Process -FilePath $InstallerPath -ArgumentList "/S" -Verb RunAs
        
        Write-Host "Installer started. This window will close."
        Stop-Transcript
        exit
    } catch {
        Write-Error "Failed to download or run installer: $_"
        Write-Host "Please download the latest release manually from GitHub."
    }
}

# 2. Stop Server
Write-Host "`n[2/4] Stopping running services..."
try {
    # Try to stop by PID first
    if (Test-Path "server.pid") {
        $PidContent = Get-Content "server.pid"
        if ($PidContent -match "^\d+$") {
            $ServerPid = [int]$PidContent
            Write-Host "Found PID file: $ServerPid"
            Stop-Process -Id $ServerPid -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped server process (PID: $ServerPid)."
        }
    }

    Stop-Process -Name "YtDlpApiServer" -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped YtDlpApiServer task/process."
    
    Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped cloudflared."
} catch {
    Write-Warning "Error stopping processes: $_"
}

# 3. Rebuild (Run setup_full.ps1)
Write-Host "`n[3/4] Rebuilding application..."
try {
    if (Test-Path "setup_full.ps1") {
        & .\setup_full.ps1
    } else {
        throw "setup_full.ps1 not found!"
    }
} catch {
    Write-Error "Build failed: $_"
    Stop-Transcript
    exit
}

# 4. Restart Server
Write-Host "`n[4/4] Restarting server..."
try {
    Start-ScheduledTask -TaskName "YtDlpApiServer" -ErrorAction Stop
    Write-Host "Server restarted successfully via Scheduled Task." -ForegroundColor Green
} catch {
    Write-Warning "Could not start Scheduled Task. Trying direct start..."
    if (Test-Path "start_server_hidden.vbs") {
        Invoke-Item "start_server_hidden.vbs"
    }
}

Write-Host "`n=== Update Complete ===" -ForegroundColor Cyan
Stop-Transcript
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
