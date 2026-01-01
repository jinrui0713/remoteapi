# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    exit
}

$TaskName = "YtDlpApiServer"
# Find exe file in the same directory
$ExePath = Join-Path $PSScriptRoot "YtDlpApiServer.exe"

if (-not (Test-Path $ExePath)) {
    Write-Error "Executable not found: $ExePath"
    exit
}

Write-Host "Executable Path: $ExePath"

# Define Action (Run exe directly)
$Action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $PSScriptRoot

# Define Trigger (At Startup)
$Trigger = New-ScheduledTaskTrigger -AtStartup

# Define Principal (SYSTEM account)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Define Settings
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

# Register Task
try {
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "Task '$TaskName' registered successfully." -ForegroundColor Green
    Write-Host "It will start automatically on next reboot."
} catch {
    Write-Error "Failed to register task: $_"
}

# Firewall Settings
$FirewallRuleName = "YtDlpApiServer"
$Port = 8000

Write-Host "Checking firewall settings..."

# Remove existing rule (prevent duplicates)
Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue

# Add new rule
try {
    New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Any
    Write-Host "ファイアウォールのルール '$FirewallRuleName' (Port $Port) を追加しました。" -ForegroundColor Green
} catch {
    Write-Error "ファイアウォールのルール追加に失敗しました: $_"
}

