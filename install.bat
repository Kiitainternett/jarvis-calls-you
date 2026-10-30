@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Install Python 3.10+ from python.org first & pause & exit /b)
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
if not exist schedule.json copy schedule.example.json schedule.json
echo.
echo 1) Fill in .env  (notepad .env)
echo 2) python -m callme login
echo 3) run.bat
pause
