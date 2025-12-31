@echo off
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

:: ファイルのコピー
copy dist\YtDlpApiServer.exe release\
copy setup_exe.ps1 release\setup.ps1
copy README.md release\README.txt

:: downloadsフォルダの作成（空）
mkdir release\downloads

echo.
echo ========================================================
echo Build Complete!
echo The installer package is located in the "release" folder.
echo ========================================================
echo.
pause
