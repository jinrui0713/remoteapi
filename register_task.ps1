$TaskName = "YtDlpApiServer"
$ExePath = "$env:LOCALAPPDATA\YtDlpApiServer\YtDlpApiServer.exe"
$WorkDir = "$env:LOCALAPPDATA\YtDlpApiServer"

Write-Host "Registering Task: $TaskName"
Write-Host "Exe: $ExePath"

if (-not (Test-Path $ExePath)) {
    Write-Error "Executable not found at $ExePath"
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $WorkDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

try {
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "Task Registered Successfully."
} catch {
    Write-Error "Failed to register task: $_"
    exit 1
}

Start-Sleep -Seconds 3
