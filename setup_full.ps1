# 管理者権限チェック
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Warning "このスクリプトは管理者権限で実行する必要があります。"
    exit
}

$ErrorActionPreference = "Stop"
$ScriptPath = $PSScriptRoot

Write-Host "=== 自動セットアップツールを開始します ===" -ForegroundColor Cyan

# 1. Python依存関係のインストール
Write-Host "`n[1/5] Pythonライブラリをインストール中..."
try {
    pip install -r "$ScriptPath\requirements.txt"
    pip install pyinstaller
} catch {
    Write-Error "ライブラリのインストールに失敗しました。Pythonがインストールされているか確認してください。"
    exit
}

# 2. ffmpegのセットアップ
$FFmpegExe = Join-Path $ScriptPath "ffmpeg.exe"
if (-not (Test-Path $FFmpegExe)) {
    Write-Host "`n[2/5] ffmpegをダウンロード中..."
    $FFmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $ZipPath = Join-Path $ScriptPath "ffmpeg.zip"
    
    try {
        Invoke-WebRequest -Uri $FFmpegUrl -OutFile $ZipPath
        
        Write-Host "ffmpegを解凍中..."
        Expand-Archive -Path $ZipPath -DestinationPath "$ScriptPath\ffmpeg_temp" -Force
        
        # ffmpeg.exeを移動
        $ExtractedPath = Get-ChildItem "$ScriptPath\ffmpeg_temp" -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
        Move-Item $ExtractedPath.FullName -Destination $FFmpegExe -Force
        
        # 後始末
        Remove-Item $ZipPath -Force
        Remove-Item "$ScriptPath\ffmpeg_temp" -Recurse -Force
        Write-Host "ffmpegのセットアップ完了" -ForegroundColor Green
    } catch {
        Write-Warning "ffmpegの自動ダウンロードに失敗しました。手動でffmpeg.exeを配置してください。"
    }
} else {
    Write-Host "`n[2/5] ffmpegは既に存在します。スキップします。"
}

# 3. Cloudflare Tunnel (cloudflared) のセットアップ
$CloudflaredExe = Join-Path $ScriptPath "cloudflared.exe"
if (-not (Test-Path $CloudflaredExe)) {
    Write-Host "`n[3/5] cloudflaredをダウンロード中..."
    $CloudflaredUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try {
        Invoke-WebRequest -Uri $CloudflaredUrl -OutFile $CloudflaredExe
        Write-Host "cloudflaredのセットアップ完了" -ForegroundColor Green
    } catch {
        Write-Warning "cloudflaredのダウンロードに失敗しました。"
    }
} else {
    Write-Host "`n[3/5] cloudflaredは既に存在します。スキップします。"
}

# 4. アプリケーションのビルド (exe化)
Write-Host "`n[4/5] アプリケーションをexe化しています..."
# PyInstallerでビルド (ffmpegを含める)
# --add-binary "src;dest" (Windowsではセミコロン区切り)
if (Test-Path $FFmpegExe) {
    pyinstaller --onefile --name "YtDlpApiServer" --add-binary "ffmpeg.exe;." --clean "$ScriptPath\main.py"
} else {
    Write-Warning "ffmpeg.exeが見つからないため、同梱せずにビルドします。"
    pyinstaller --onefile --name "YtDlpApiServer" --clean "$ScriptPath\main.py"
}

# distフォルダからexeをルートに移動
if (Test-Path "$ScriptPath\dist\YtDlpApiServer.exe") {
    Move-Item "$ScriptPath\dist\YtDlpApiServer.exe" "$ScriptPath\YtDlpApiServer.exe" -Force
    Remove-Item "$ScriptPath\build" -Recurse -Force
    Remove-Item "$ScriptPath\YtDlpApiServer.spec" -Force
    if (Test-Path "$ScriptPath\dist") { Remove-Item "$ScriptPath\dist" -Recurse -Force }
    Write-Host "ビルド完了: YtDlpApiServer.exe" -ForegroundColor Green
} else {
    Write-Error "ビルドに失敗しました。"
    exit
}

# 5. 起動用スクリプトの作成
Write-Host "`n[5/5] 起動用スクリプトを作成中..."
$StartScript = @"
@echo off
cd /d "%~dp0"
echo Starting API Server...
start "" "YtDlpApiServer.exe"

echo.
echo ========================================================
echo Starting Cloudflare Tunnel...
echo This will generate a public URL for your local server.
echo Look for the URL ending in .trycloudflare.com below.
echo ========================================================
echo.
cloudflared.exe tunnel --url http://localhost:8000
pause
"@
Set-Content -Path "$ScriptPath\start_public.bat" -Value $StartScript

Write-Host "`n=== セットアップが完了しました！ ===" -ForegroundColor Cyan
Write-Host "以下のファイルが作成されました:"
Write-Host "1. YtDlpApiServer.exe (サーバー本体)"
Write-Host "2. cloudflared.exe (公開用ツール)"
Write-Host "3. start_public.bat (公開用起動スクリプト)"
Write-Host "`n[使い方]"
Write-Host "'start_public.bat' をダブルクリックすると、サーバーが起動し世界中に公開されます。"
Write-Host "黒い画面に表示される 'https://xxxx-xxxx.trycloudflare.com' というURLにアクセスしてください。"
