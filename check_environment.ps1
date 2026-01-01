# check_environment.ps1

Write-Host "=== System & Network Environment Check Tool ===" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date)"

# 1. Administrator Privileges
Write-Host "`n[1] Checking Administrator Privileges..."
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if ($IsAdmin) {
    Write-Host "PASS: Running as Administrator" -ForegroundColor Green
} else {
    Write-Host "WARNING: Not running as Administrator. Some checks or setups might fail." -ForegroundColor Yellow
}

# 2. PowerShell Execution Policy
Write-Host "`n[2] Checking Execution Policy..."
try {
    $Policy = Get-ExecutionPolicy
    Write-Host "Current Policy: $Policy"
} catch {
    Write-Host "FAIL: Could not check Execution Policy" -ForegroundColor Red
}

# 3. Network Connectivity (Ping)
Write-Host "`n[3] Checking Internet Connectivity (Ping)..."
$PingTargets = @("8.8.8.8", "1.1.1.1")
foreach ($Target in $PingTargets) {
    try {
        $Ping = Test-Connection -ComputerName $Target -Count 1 -ErrorAction Stop
        Write-Host "PASS: Ping to $Target successful" -ForegroundColor Green
    } catch {
        Write-Host "FAIL: Ping to $Target failed" -ForegroundColor Red
    }
}

# 4. DNS Resolution
Write-Host "`n[4] Checking DNS Resolution..."
$Domains = @("google.com", "github.com", "pypi.org", "www.gyan.dev")
foreach ($Domain in $Domains) {
    try {
        $IP = [System.Net.Dns]::GetHostAddresses($Domain)
        Write-Host "PASS: Resolved $Domain" -ForegroundColor Green
    } catch {
        Write-Host "FAIL: Could not resolve $Domain" -ForegroundColor Red
    }
}

# 5. HTTP/HTTPS Connectivity & TLS
Write-Host "`n[5] Checking HTTP Access & TLS..."
# Ensure TLS 1.2 is enabled for the test
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
