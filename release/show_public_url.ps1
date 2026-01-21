# show_public_url.ps1
$LogFile = Join-Path $PSScriptRoot "cloudflared.log"

if (-not (Test-Path $LogFile)) {
    Write-Warning "ログファイルが見つかりません。まだ 'start_public_hidden.vbs' を実行していないか、起動に失敗しています。"
    exit
}

Write-Host "ログファイルを解析中..." -ForegroundColor Cyan

# ログファイルからURLを検索
$Found = $false
Get-Content $LogFile -Wait -Tail 10 | ForEach-Object {
    if ($_ -match "https://[a-zA-Z0-9-]+\.trycloudflare\.com") {
        Write-Host "`n========================================================" -ForegroundColor Green
        Write-Host "公開URLが見つかりました！" -ForegroundColor Green
        Write-Host $matches[0] -ForegroundColor Yellow
        Write-Host "========================================================" -ForegroundColor Green
        $Found = $true
        # ループを抜ける（Ctrl+Cで終了させるため、ここではBreakできないが、ユーザーに通知）
        Write-Host "確認できたら Ctrl+C で終了してください。"
    }
}
