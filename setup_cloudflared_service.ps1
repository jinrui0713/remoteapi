# setup_cloudflared_service.ps1

# 管理者権限チェック
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "このスクリプトは管理者権限で実行する必要があります。"
    exit
}

Write-Host "=== Cloudflare Tunnel サービス化ツール ===" -ForegroundColor Cyan
Write-Host "Cloudflare Zero Trustで取得したトークンを使用して、"
Write-Host "トンネルをWindowsサービスとしてインストールします。"
Write-Host "※ これを行うには、Cloudflareのアカウントとドメインが必要です。"
Write-Host ""

$Token = Read-Host "トークンを入力してください (eyJh...)"

if ([string]::IsNullOrWhiteSpace($Token)) {
    Write-Error "トークンが入力されていません。"
    exit
}

$CloudflaredExe = Join-Path $PSScriptRoot "cloudflared.exe"

if (-not (Test-Path $CloudflaredExe)) {
    Write-Error "cloudflared.exe が見つかりません。setup_full.ps1 を先に実行してください。"
    exit
}

try {
    Write-Host "サービスをインストールしています..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service install $Token" -Wait -NoNewWindow
    
    Write-Host "サービスを開始しています..."
    Start-Process -FilePath $CloudflaredExe -ArgumentList "service start" -Wait -NoNewWindow
    
    Write-Host "`n[成功] サービス化が完了しました！" -ForegroundColor Green
    Write-Host "PCを再起動しても自動的に接続されます。"
    Write-Host "URLはCloudflareのダッシュボードで設定したものが固定で使えます。"
} catch {
    Write-Error "エラーが発生しました: $_"
}
