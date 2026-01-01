@echo off
chcp 65001 > nul
cd /d %~dp0

REM Activate venv if exists
if exist venv\Scripts\activate.bat call venv\Scripts\activate.bat

REM Start server
python main.py
pause
