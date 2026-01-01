# update_app.ps1

# 管理者権限チェック
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "This script must be run as Administrator."
    exit
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot
Set-Location $ScriptPath

Start-Transcript -Path "update_log.txt" -Force

Write-Host "=== Starting Update Process ===" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date)"

# 1. Git Pull
Write-Host "`n[1/4] Checking for updates from GitHub..."
try {
    # フェッチして差分確認
    git fetch origin main
    $LocalHash = git rev-parse HEAD
    $RemoteHash = git rev-parse origin/main

    if ($LocalHash -eq $RemoteHash) {
        Write-Host "Already up to date." -ForegroundColor Green
        Stop-Transcript
        exit
    }

    Write-Host "New version found. Updating..." -ForegroundColor Yellow
    # 強制的にリモートに合わせる（ローカルの変更は破棄）
    git reset --hard origin/main
    git pull origin main
} catch {
    Write-Error "Failed to update from git. Please check git installation and network."
    Write-Error $_
    Stop-Transcript
    exit
}

# 2. Stop Server
Write-Host "`n[2/4] Stopping running services..."
try {
    Stop-Process -Name "YtDlpApiServer" -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped YtDlpApiServer."
    
    # cloudflaredも一度止める（設定変更などの可能性があるため）
    Stop-Process -Name "cloudflared" -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped cloudflared."
} catch {
    Write-Warning "Error stopping processes: $_"
}

# 3. Rebuild (Run setup_full.ps1)
Write-Host "`n[3/4] Rebuilding application..."
try {
    # setup_full.ps1 を実行
    # 注意: setup_full.ps1 は依存関係のインストールやビルドを行う
    & .\setup_full.ps1
} catch {
    Write-Error "Build failed: $_"
    Stop-Transcript
    exit
}

# 4. Restart Server
Write-Host "`n[4/4] Restarting server..."
try {
    # タスクスケジューラに登録されているはずなので、それを開始する
    Start-ScheduledTask -TaskName "YtDlpApiServer"
    Write-Host "Server restarted successfully via Scheduled Task." -ForegroundColor Green
} catch {
    Write-Warning "Could not start Scheduled Task. Trying direct start..."
    # フォールバック: 直接起動（非表示）
    if (Test-Path "start_server_hidden.vbs") {
        Invoke-Item "start_server_hidden.vbs"
    }
}

Write-Host "`n=== Update Complete ===" -ForegroundColor Cyan
Stop-Transcript
