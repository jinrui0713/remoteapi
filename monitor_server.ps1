$ExePath = "$env:LOCALAPPDATA\YtDlpApiServer\YtDlpApiServer.exe"
$ProcessName = "YtDlpApiServer"

Write-Host "Starting Server Monitor..."
Write-Host "Target: $ExePath"

while ($true) {
    $p = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
    if (-not $p) {
        Write-Host "$(Get-Date): Server process not found. Starting..."
        if (Test-Path $ExePath) {
            Start-Process -FilePath $ExePath -WindowStyle Hidden
            Write-Host "$(Get-Date): Server started."
        } else {
            Write-Error "$(Get-Date): Executable not found at $ExePath"
        }
    }
    Start-Sleep -Seconds 10
}
