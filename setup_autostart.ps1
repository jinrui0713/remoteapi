# 管理者権限チェック
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "このスクリプトは管理者権限で実行する必要があります。"
    exit
}

$TaskName = "YtDlpApiServer"
$ScriptPath = Join-Path $PSScriptRoot "main.py"
$PythonPath = (Get-Command python).Source

if (-not $PythonPath) {
    Write-Error "Pythonが見つかりません。Pythonをインストールし、PATHに追加してください。"
    exit
}

Write-Host "Python Path: $PythonPath"
Write-Host "Script Path: $ScriptPath"

# アクションの定義 (python main.py を実行)
# WorkingDirectoryを指定することで、相対パスのdownloadsフォルダなどが正しく機能するようにする
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument $ScriptPath -WorkingDirectory $PSScriptRoot

# トリガーの定義 (システム起動時)
$Trigger = New-ScheduledTaskTrigger -AtStartup

# プリンシパルの定義 (SYSTEMアカウントで実行、最高特権)
# SYSTEMアカウントで実行することで、ログインしていなくてもバックグラウンドで動作します
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# 設定の定義 (電源接続時のみなどの制限を解除)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 -Hidden

# タスクの登録
try {
    Register-ScheduledTask -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -TaskName $TaskName -Description "Runs yt-dlp API server automatically at startup." -Force
    Write-Host "タスク '$TaskName' が正常に登録されました。" -ForegroundColor Green
    Write-Host "PCを再起動すると自動的に開始されます。"
} catch {
    Write-Error "タスクの登録に失敗しました: $_"
}
