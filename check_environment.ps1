# check_environment.ps1

Write-Host "=== System & Network Environment Check Tool ===" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date)"

# AppData Paths
$AppDataDir = Join-Path $env:LOCALAPPDATA "YtDlpApiServer"
$LogFile = Join-Path $AppDataDir "server.log"
$ExePath = Join-Path $AppDataDir "YtDlpApiServer.exe"

# 1. Process Status
Write-Host "`n[1] Checking Server Process..."
$Process = Get-Process -Name "YtDlpApiServer" -ErrorAction SilentlyContinue
if ($Process) {
    Write-Host "PASS: Server is running (PID: $($Process.Id))" -ForegroundColor Green
} else {
    Write-Host "FAIL: Server process is NOT running." -ForegroundColor Red
}

# 2. Port Status
Write-Host "`n[2] Checking Port 8000..."
$Port = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($Port) {
    Write-Host "PASS: Port 8000 is listening." -ForegroundColor Green
} else {
    Write-Host "FAIL: Port 8000 is NOT listening." -ForegroundColor Red
}

# 3. Cloudflared Service
Write-Host "`n[3] Checking Cloudflared Service..."
$Service = Get-Service -Name "Cloudflared" -ErrorAction SilentlyContinue
if ($Service) {
    Write-Host "Service Status: $($Service.Status)"
    if ($Service.Status -eq "Running") {
        Write-Host "PASS: Cloudflared service is running." -ForegroundColor Green
    } else {
        Write-Host "FAIL: Cloudflared service is stopped." -ForegroundColor Red
    }
} else {
    Write-Host "FAIL: Cloudflared service not found." -ForegroundColor Red
}

# 4. Log File Analysis
Write-Host "`n[4] Checking Server Log..."
if (Test-Path $LogFile) {
    Write-Host "Log file found at: $LogFile"
    Write-Host "--- Last 20 lines ---" -ForegroundColor Gray
    Get-Content $LogFile -Tail 20
    Write-Host "---------------------" -ForegroundColor Gray
} else {
    Write-Host "FAIL: Log file not found." -ForegroundColor Red
}

# 5. Manual Start Test
if (-not $Process) {
    Write-Host "`n[5] Attempting Manual Start Test..."
    Write-Host "Running exe directly to capture output..."
    try {
        Push-Location $AppDataDir
        & $ExePath
        Pop-Location
    } catch {
        Write-Error "Failed to run exe: $_"
    }
}

Write-Host "`nDone. Please share the output above."
Pause
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host "Enabled Protocols: $([Net.ServicePointManager]::SecurityProtocol)"

$Urls = @(
    "https://www.google.com",
    "https://github.com",
    "https://pypi.org",
    "https://www.gyan.dev",
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)

foreach ($Url in $Urls) {
    try {
        $Request = [System.Net.WebRequest]::Create($Url)
        $Request.Method = "HEAD"
        $Request.Timeout = 10000
        $Request.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        
        $Response = $Request.GetResponse()
        $StatusCode = [int]$Response.StatusCode
        $Response.Close()
        
        if ($StatusCode -ge 200 -and $StatusCode -lt 400) {
            Write-Host "PASS: Access to $Url (Status: $StatusCode)" -ForegroundColor Green
        } else {
            Write-Host "WARNING: Access to $Url returned status $StatusCode" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "FAIL: Could not access $Url. Error: $_" -ForegroundColor Red
    }
}

# 6. Proxy Settings
Write-Host "`n[6] Checking Proxy Settings..."
try {
    $Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
    $Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
    $TestUri = [Uri]"https://www.google.com"
    $ProxyUri = $Proxy.GetProxy($TestUri)
    
    if ($ProxyUri -ne $TestUri) {
        Write-Host "INFO: Proxy detected: $ProxyUri" -ForegroundColor Yellow
    } else {
        Write-Host "PASS: No system proxy detected" -ForegroundColor Green
    }
} catch {
    Write-Host "WARNING: Could not check proxy settings" -ForegroundColor Yellow
}

Write-Host "`n=== Check Complete ===" -ForegroundColor Cyan
Read-Host "Press Enter to exit..."
