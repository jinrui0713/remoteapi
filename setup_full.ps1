# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    exit
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot
Set-Location $ScriptPath

# Enable TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Robust Download Function ---
function Download-File {
    param (
        [string]$Url,
        [string]$OutputFile
    )

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
            [System.Net.WebRequest]::DefaultWebProxy = $OldProxy
            Write-Host " Failed ($($_))" -ForegroundColor Yellow
        }
    }

    # Method 3: curl.exe (External tool, often handles SSL/Proxy better)
    if (-not $Downloaded -and (Get-Command "curl.exe" -ErrorAction SilentlyContinue)) {
        try {
            Write-Host "  [Attempt 3] Using curl.exe..." -NoNewline
            # -L: Follow redirects, -k: Insecure (skip SSL check), -f: Fail silently on error
            $argList = "-L", "-k", "-f", $Url, "-o", $OutputFile, "-A", $UserAgent
            $p = Start-Process "curl.exe" -ArgumentList $argList -Wait -NoNewWindow -PassThru
            
            if ($p.ExitCode -eq 0 -and (Test-Path $OutputFile) -and (Get-Item $OutputFile).Length -gt 0) {
                Write-Host " Success" -ForegroundColor Green
                $Downloaded = $true
            } else {
                Write-Host " Failed (Exit Code: $($p.ExitCode))" -ForegroundColor Yellow
            }
        } catch {
            Write-Host " Failed ($($_))" -ForegroundColor Yellow
        }
    }

    # Method 4: Invoke-WebRequest with SSL Validation Disabled (Last Resort)
    if (-not $Downloaded) {
        try {
            Write-Host "  [Attempt 4] Disabling SSL validation..." -NoNewline
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}
            Invoke-WebRequest -Uri $Url -OutFile $OutputFile -UserAgent $UserAgent -ErrorAction Stop
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $null
            Write-Host " Success" -ForegroundColor Green
            $Downloaded = $true
        } catch {
            [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $null
            Write-Host " Failed ($($_))" -ForegroundColor Red
        }
    }

    if (-not $Downloaded) {
        throw "All download methods failed for $Url"
    }
}
# --------------------------------

Write-Host "=== Starting Auto Setup Tool (Robust Mode) ===" -ForegroundColor Cyan

# 1. Install Python dependencies
Write-Host "`n[1/5] Installing Python libraries..."
try {
    # pip usually handles proxies well, but if it fails, user might need to set HTTP_PROXY env var
    pip install -r "requirements.txt"
    pip install pyinstaller
}
catch {
    Write-Error "Failed to install libraries. Please check if Python is installed."
    exit
}

# 2. Setup ffmpeg
$FFmpegExe = Join-Path $ScriptPath "ffmpeg.exe"
if (-not (Test-Path $FFmpegExe)) {
    Write-Host "`n[2/5] Downloading ffmpeg..."
    $FFmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $ZipPath = Join-Path $ScriptPath "ffmpeg.zip"
    $TempDir = Join-Path $ScriptPath "ffmpeg_temp"
    
    try {
        Download-File -Url $FFmpegUrl -OutputFile $ZipPath
        
        Write-Host "Extracting ffmpeg..."
        Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
        
        # Move ffmpeg.exe
        $ExtractedPath = Get-ChildItem -Path $TempDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        
        if ($ExtractedPath) {
            Move-Item -Path $ExtractedPath.FullName -Destination $FFmpegExe -Force
        }
        else {
            throw "ffmpeg.exe not found in the downloaded archive."
        }
        
        # Cleanup
        if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
        if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force }
        
        Write-Host "ffmpeg setup complete." -ForegroundColor Green
    }
    catch {
        Write-Warning "Failed to download ffmpeg automatically. Please place ffmpeg.exe manually. Error: $_"
    }
}
else {
    Write-Host "`n[2/5] ffmpeg already exists. Skipping."
}

# 3. Setup Cloudflare Tunnel (cloudflared)
$CloudflaredExe = Join-Path $ScriptPath "cloudflared.exe"
if (-not (Test-Path $CloudflaredExe)) {
    Write-Host "`n[3/5] Downloading cloudflared..."
    $CloudflaredUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try {
        Download-File -Url $CloudflaredUrl -OutputFile $CloudflaredExe
        Write-Host "cloudflared setup complete." -ForegroundColor Green
    }
    catch {
        Write-Warning "Failed to download cloudflared."
    }
}
else {
    Write-Host "`n[3/5] cloudflared already exists. Skipping."
}

# 4. Build Application (exe)
Write-Host "`n[4/5] Building application..."
# Build with PyInstaller (include ffmpeg)
# --noconsole: Hide the console window
if (Test-Path $FFmpegExe) {
    pyinstaller --noconsole --onefile --name "YtDlpApiServer" --add-binary "ffmpeg.exe;." --add-data "static;static" --clean "main.py"
}
else {
    Write-Warning "ffmpeg.exe not found. Building without it."
    pyinstaller --noconsole --onefile --name "YtDlpApiServer" --add-data "static;static" --clean "main.py"
}

# Move exe from dist to root
$DistExe = Join-Path $ScriptPath "dist\YtDlpApiServer.exe"
$TargetExe = Join-Path $ScriptPath "YtDlpApiServer.exe"

if (Test-Path $DistExe) {
    Move-Item -Path $DistExe -Destination $TargetExe -Force
    
    # Cleanup
    if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
    if (Test-Path "YtDlpApiServer.spec") { Remove-Item "YtDlpApiServer.spec" -Force }
    if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
    
    Write-Host "Build complete: YtDlpApiServer.exe" -ForegroundColor Green
}
else {
    Write-Error "Build failed."
    exit
}

# 5. Create Start Scripts
Write-Host "`n[5/5] Creating start scripts..."

# --- start_public.bat (Visible for debugging) ---
$StartScript = @"
@echo off
cd /d "%~dp0"
echo Starting API Server...
start "" "YtDlpApiServer.exe"

echo.
echo ========================================================
echo Starting Cloudflare Tunnel...
echo This will generate a public URL for your local server.
echo Look for the URL ending in .trycloudflare.com below.
echo ========================================================
echo.
cloudflared.exe tunnel --url http://localhost:8000
pause
"@
Set-Content -Path "$ScriptPath\start_public.bat" -Value $StartScript

# --- start_hidden.vbs (Completely Hidden) ---
$HiddenScript = @"
Set WshShell = CreateObject("WScript.Shell")
' Run start_public.bat hidden (0)
' Note: This will hide the cloudflared window too, so you won't see the URL.
' Use this only if you have a fixed tunnel or just want local access.
' For now, let's just run the server hidden and cloudflared visible if needed.
' Actually, user asked for "completely background".
' So we will run the server directly.
WshShell.Run "YtDlpApiServer.exe", 0, False
"@
Set-Content -Path "$ScriptPath\start_server_hidden.vbs" -Value $HiddenScript

# --- Register Scheduled Task for Auto-Start ---
$TaskName = "YtDlpApiServer"
$Action = New-ScheduledTaskAction -Execute "$ScriptPath\YtDlpApiServer.exe" -WorkingDirectory $ScriptPath
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "Auto-start task registered successfully." -ForegroundColor Green
} catch {
    Write-Warning "Failed to register auto-start task. You may need to run this script as Administrator."
}

Write-Host "`n=== Setup Complete! ===" -ForegroundColor Cyan
Write-Host "The following files were created:"
Write-Host "1. YtDlpApiServer.exe (Server - No Console)"
Write-Host "2. start_public.bat (Public Access - Visible)"
Write-Host "3. start_server_hidden.vbs (Local Access - Hidden)"
Write-Host "`n[Usage]"
Write-Host "- To start publicly: Run 'start_public.bat'"
Write-Host "- To start locally (hidden): Run 'start_server_hidden.vbs'"
Write-Host "- Auto-start: The server is now scheduled to start automatically when Windows boots."

