@echo off
chcp 65001 > nul
cd /d %~dp0

echo Building YtDlpApiServer...

REM Check and create virtual environment
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Build with PyInstaller
REM --onefile: Bundle into a single exe
REM --noconsole: Do not show console window (for background execution)
REM --name: Output file name
echo Running PyInstaller for Server...
pyinstaller --onefile --name YtDlpApiServer --clean main.py

REM Create release package
echo Creating release package...
if exist release rmdir /s /q release
mkdir release

REM Download and bundle FFmpeg
echo.
if exist "bin\ffmpeg.exe" (
    echo Found FFmpeg in bin folder. Copying...
    if not exist ffmpeg_temp\bin mkdir ffmpeg_temp\bin
    copy "bin\ffmpeg.exe" "ffmpeg_temp\bin\"
    copy "bin\ffprobe.exe" "ffmpeg_temp\bin\"
) else (
    echo Downloading FFmpeg...
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'ffmpeg.zip'"

    echo Extracting FFmpeg...
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Expand-Archive -Path 'ffmpeg.zip' -DestinationPath 'ffmpeg_temp' -Force"
    del ffmpeg.zip
    
    REM Move to temp bin structure for consistent copying below
    move ffmpeg_temp\ffmpeg-master-latest-win64-gpl\bin ffmpeg_temp\bin
)

REM Download Cloudflared
if not exist "bin" mkdir "bin"
if not exist "bin\cloudflared.exe" (
    echo Downloading Cloudflared...
    powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'bin\cloudflared.exe'"
)
if not exist "ffmpeg_temp\bin" mkdir "ffmpeg_temp\bin"
copy "bin\cloudflared.exe" "ffmpeg_temp\bin\"

echo Copying files to release staging...
copy dist\YtDlpApiServer.exe release\
move "ffmpeg_temp\bin\ffmpeg.exe" release\
move "ffmpeg_temp\bin\ffprobe.exe" release\
move "ffmpeg_temp\bin\cloudflared.exe" release\
xcopy /E /I static release\static
copy start_public_hidden.vbs release\
copy show_public_url.ps1 release\
copy update_app.ps1 release\
copy setup_full.ps1 release\

REM Cleanup intermediate files to save space
rmdir /s /q ffmpeg_temp
rmdir /s /q build
del dist\YtDlpApiServer.exe

REM Build Installer
echo Building Installer...
REM --add-data: Include files in the installer exe
REM Format: source;dest
pyinstaller --onefile --name Setup --clean --noconsole ^
    --add-data "release\YtDlpApiServer.exe;." ^
    --add-data "release\ffmpeg.exe;." ^
    --add-data "release\ffprobe.exe;." ^
    --add-data "release\cloudflared.exe;." ^
    --add-data "release\start_public_hidden.vbs;." ^
    --add-data "release\show_public_url.ps1;." ^
    --add-data "release\update_app.ps1;." ^
    --add-data "release\setup_full.ps1;." ^
    --add-data "release\static;static" ^
    installer.py

REM Cleanup
copy dist\Setup.exe release\Setup.exe
if exist ffmpeg.zip del ffmpeg.zip
rmdir /s /q ffmpeg_temp

echo.
echo ========================================================
echo Build Complete!
echo.
echo The "release" folder now contains:
echo  - Setup.exe (Single-file Installer)
echo.
echo Run Setup.exe to install the server.
echo ========================================================
echo.
rem pause
