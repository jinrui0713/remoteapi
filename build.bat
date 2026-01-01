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
echo Running PyInstaller...
pyinstaller --onefile --name YtDlpApiServer --clean main.py

REM Create release package
echo Creating release package...
if exist release rmdir /s /q release
mkdir release

REM Download and bundle FFmpeg
echo.
echo Downloading FFmpeg (this may take a while)...
powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'ffmpeg.zip'"

echo Extracting FFmpeg...
powershell -Command "$ProgressPreference = 'SilentlyContinue'; Expand-Archive -Path 'ffmpeg.zip' -DestinationPath 'ffmpeg_temp' -Force"

echo Copying FFmpeg binaries...
REM Adjust path to match extracted folder structure (usually in ffmpeg-master-latest-win64-gpl/bin/)
xcopy /y "ffmpeg_temp\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe" release\
xcopy /y "ffmpeg_temp\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe" release\

REM Remove temporary files
del ffmpeg.zip
rmdir /s /q ffmpeg_temp

REM Copy files
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
