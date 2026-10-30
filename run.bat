@echo off
chcp 65001 >nul
cd /d "%~dp0"
call .venv\Scripts\activate
python -m callme serve
pause
