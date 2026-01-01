# setup_cloudflared_service.ps1

# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    Write-Host "Please right-click and select 'Run as administrator'." -ForegroundColor Yellow
    Pause
    exit
}

Write-Host "=== Cloudflare Tunnel Service Setup ===" -ForegroundColor Cyan
Write-Host "This script installs the tunnel as a Windows service using your Cloudflare Zero Trust token."
Write-Host "Note: You need a Cloudflare account and domain for this."
Write-Host ""

$Token = Read-Host "Enter your Token (starts with eyJh...)"

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Error "Token is empty."
    Pause
    exit
}

$CloudflaredExe = Join-Path $PSScriptRoot "cloudflared.exe"

if (-not (Test-Path $CloudflaredExe)) {
    Write-Error "cloudflared.exe not found. Please run setup_full.ps1 first."
    Pause
    exit
}

try {
    Write-Host "Installing service..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service install $Token" -Wait -NoNewWindow
    
    Write-Host "Starting service..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service start" -Wait -NoNewWindow
    
    Write-Host "`n[Success] Service installed successfully!" -ForegroundColor Green
    Write-Host "It will connect automatically even after reboot."
    Write-Host "The URL configured in Cloudflare Dashboard will be persistent."
} catch {
    Write-Error "An error occurred: $_"
}

Write-Host "`nPress any key to exit..."
Pause
