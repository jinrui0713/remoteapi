$TaskName = "YtDlpApiServer"
$MonitorScript = "$env:LOCALAPPDATA\YtDlpApiServer\monitor_server.ps1"
$AppDataDir = "$env:LOCALAPPDATA\YtDlpApiServer"

# Copy monitor script to AppData
Copy-Item -Path "monitor_server.ps1" -Destination $MonitorScript -Force

Write-Host "Registering Task: $TaskName"
Write-Host "Monitor Script: $MonitorScript"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$MonitorScript`"" -WorkingDirectory $AppDataDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

try {
    # Unregister old task if exists
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server monitor automatically at startup." -Force
    Write-Host "Task Registered Successfully."
} catch {
    Write-Error "Failed to register task: $_"
    exit 1
}

Start-Sleep -Seconds 3
