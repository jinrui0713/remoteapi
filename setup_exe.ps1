# 管理者権限チェック
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "このスクリプトは管理者権限で実行する必要があります。"
    exit
}

$TaskName = "YtDlpApiServer"
# このスクリプトと同じ場所にあるexeファイルを探す
$ExePath = Join-Path $PSScriptRoot "YtDlpApiServer.exe"

if (-not (Test-Path $ExePath)) {
    Write-Error "実行ファイルが見つかりません: $ExePath"
    exit
}

Write-Host "Executable Path: $ExePath"

# アクションの定義 (exeを直接実行)
$Action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $PSScriptRoot

# トリガーの定義 (システム起動時)
$Trigger = New-ScheduledTaskTrigger -AtStartup

# プリンシパルの定義 (SYSTEMアカウントで実行)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# 設定の定義
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

# タスクの登録
try {
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "タスク '$TaskName' が正常に登録されました。" -ForegroundColor Green
    Write-Host "PCを再起動すると自動的に開始されます。"
} catch {
    Write-Error "タスクの登録に失敗しました: $_"
}

# ファイアウォールの設定
$FirewallRuleName = "YtDlpApiServer"
$Port = 8000

Write-Host "ファイアウォールの設定を確認しています..."

# 既存のルールを確認して削除（重複防止）
Remove-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue

# 新しいルールを追加
try {
    New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Any
    Write-Host "ファイアウォールのルール '$FirewallRuleName' (Port $Port) を追加しました。" -ForegroundColor Green
} catch {
    Write-Error "ファイアウォールのルール追加に失敗しました: $_"
}

