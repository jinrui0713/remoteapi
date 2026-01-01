# Check for Administrator privileges
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    exit
}

$TaskName = "YtDlpApiServer"

# Determine Script Path
$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) {
    $ScriptRoot = Get-Location
}
$ScriptPath = Join-Path $ScriptRoot "main.py"

# Determine Python Path
# 1. Check for venv
$VenvPython = Join-Path $ScriptRoot "venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonPath = $VenvPython
} else {
    # 2. Check system python
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        $PythonPath = $PythonCmd.Source
        if ($PythonPath -is [array]) {
            $PythonPath = $PythonPath[0]
        }
    }
}

if (-not $PythonPath -or -not (Test-Path $PythonPath)) {
    Write-Error "Python not found. Please install Python or create a venv."
    exit 1
}

Write-Host "Python Path: $PythonPath"
Write-Host "Script Path: $ScriptPath"

# Define Action
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument $ScriptPath -WorkingDirectory $ScriptRoot

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
