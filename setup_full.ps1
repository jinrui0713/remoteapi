# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    exit
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot
Set-Location $ScriptPath

# Enable TLS 1.2 (Required for GitHub and other modern sites)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "=== Starting Auto Setup Tool ===" -ForegroundColor Cyan

# 1. Install Python dependencies
Write-Host "`n[1/5] Installing Python libraries..."
try {
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
        Invoke-WebRequest -Uri $FFmpegUrl -OutFile $ZipPath -UserAgent "Mozilla/5.0"
        
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
        Invoke-WebRequest -Uri $CloudflaredUrl -OutFile $CloudflaredExe -UserAgent "Mozilla/5.0"
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
if (Test-Path $FFmpegExe) {
    pyinstaller --onefile --name "YtDlpApiServer" --add-binary "ffmpeg.exe;." --clean "main.py"
}
else {
    Write-Warning "ffmpeg.exe not found. Building without it."
    pyinstaller --onefile --name "YtDlpApiServer" --clean "main.py"
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

# 5. Create Start Script
Write-Host "`n[5/5] Creating start script..."
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

Write-Host "`n=== Setup Complete! ===" -ForegroundColor Cyan
Write-Host "The following files were created:"
Write-Host "1. YtDlpApiServer.exe (Server)"
Write-Host "2. cloudflared.exe (Tunnel Tool)"
Write-Host "3. start_public.bat (Start Script)"
Write-Host "`n[Usage]"
Write-Host "Double-click 'start_public.bat' to start the server and expose it to the internet."
Write-Host "Look for the URL 'https://xxxx-xxxx.trycloudflare.com' in the black window."
