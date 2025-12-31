@echo off
cd /d %~dp0

:: 仮想環境がある場合は有効化 (オプション)
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat

:: サーバー起動 (ログはserver.logに出力されるようにmain.pyで設定済みですが、標準出力もリダイレクト可能)
python main.py
