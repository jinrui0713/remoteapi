@echo off
chcp 65001 > nul
cd /d %~dp0

echo Building YtDlpApiServer...

:: 仮想環境の確認と作成
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

:: 仮想環境のアクティベート
call venv\Scripts\activate

:: 依存関係のインストール
echo Installing dependencies...
pip install -r requirements.txt

:: PyInstallerによるビルド
:: --onefile: 1つのexeファイルにまとめる
:: --noconsole: コンソールウィンドウを表示しない（バックグラウンド実行用）
:: --name: 出力ファイル名
echo Running PyInstaller...
pyinstaller --onefile --name YtDlpApiServer --clean main.py

:: 配布用フォルダの作成
echo Creating release package...
if exist release rmdir /s /q release
mkdir release

:: FFmpegのダウンロードと同梱
echo.
echo Downloading FFmpeg (this may take a while)...
powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'ffmpeg.zip'"

echo Extracting FFmpeg...
powershell -Command "$ProgressPreference = 'SilentlyContinue'; Expand-Archive -Path 'ffmpeg.zip' -DestinationPath 'ffmpeg_temp' -Force"

echo Copying FFmpeg binaries...
:: 解凍されたフォルダ構造に合わせてパスを調整（通常は ffmpeg-master-latest-win64-gpl/bin/ にある）
xcopy /y "ffmpeg_temp\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" release\
xcopy /y "ffmpeg_temp\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe" release\

:: 一時ファイルの削除
del ffmpeg.zip
rmdir /s /q ffmpeg_temp

:: ファイルのコピー
copy dist\YtDlpApiServer.exe release\
copy setup_exe.ps1 release\setup.ps1
copy README.md release\README.txt

:: downloadsフォルダの作成（空）
mkdir release\downloads

echo.
echo ========================================================
echo Build Complete!
echo.
echo The "release" folder now contains:
echo  - YtDlpApiServer.exe (App)
echo  - ffmpeg.exe / ffprobe.exe (Tools)
echo  - setup.ps1 (Auto-start script)
echo.
echo You can zip and distribute the "release" folder.
echo ========================================================
echo.
pause
