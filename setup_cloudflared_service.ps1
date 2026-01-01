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

$Token = ""
$TokenFile = Join-Path $PSScriptRoot "cloudflare_token.txt"
if (Test-Path $TokenFile) {
    $Token = Get-Content $TokenFile -Raw
    $Token = $Token.Trim()
}

if ([string]::IsNullOrWhiteSpace($Token)) {
    $Token = Read-Host "Enter your Token (starts with eyJh...)"
}

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Error "Token is empty."
    Pause
    exit
}

$CloudflaredExe = Join-Path $env:LOCALAPPDATA "YtDlpApiServer\bin\cloudflared.exe"

if (-not (Test-Path $CloudflaredExe)) {
    # Fallback to local bin
    $CloudflaredExe = Join-Path $PSScriptRoot "bin\cloudflared.exe"
}

if (-not (Test-Path $CloudflaredExe)) {
    # Fallback to root
    $CloudflaredExe = Join-Path $PSScriptRoot "cloudflared.exe"
}

if (-not (Test-Path $CloudflaredExe)) {
    Write-Error "cloudflared.exe not found. Please run setup_full.ps1 first."
    Pause
    exit
}

try {
    Write-Host "Stopping existing service (if any)..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service stop" -Wait -NoNewWindow -ErrorAction SilentlyContinue
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service uninstall" -Wait -NoNewWindow -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    Write-Host "Installing service..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service install $Token" -Wait -NoNewWindow
    
    Write-Host "Starting service..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service start" -Wait -NoNewWindow
    
    Start-Sleep -Seconds 3
    $Service = Get-Service -Name "Cloudflared" -ErrorAction SilentlyContinue
    if ($Service.Status -eq "Running") {
        Write-Host "`n[Success] Service installed and running!" -ForegroundColor Green
        Write-Host "It will connect automatically even after reboot."
    } else {
        Write-Host "`n[FAIL] Service installed but NOT running." -ForegroundColor Red
        Write-Host "Attempting to run interactively to diagnose..."
        
        Write-Host "------------------------------------------------"
        Write-Host "Please check the output below for errors:"
        # Run directly to see output
        & $CloudflaredExe tunnel run --token $Token
        Write-Host "------------------------------------------------"
    }
} catch {
    Write-Error "An error occurred: $_"
}
} catch {
    Write-Error "An error occurred: $_"
}

Write-Host "`nPress any key to exit..."
Pause
