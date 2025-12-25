@echo off
cd /d %~dp0
set "PORT=9222"

echo Checking if Chrome is running on port %PORT%...
netstat -an | find ":%PORT%" >nul
if %errorlevel% neq 0 (
    echo Chrome debug port %PORT% not found. Launching Chrome...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug-profile"
    echo Waiting for Chrome to initialize...
    timeout /t 4 >nul
) else (
    echo Chrome is already running on port %PORT%.
)

echo Starting FastAPI Server...
call venv\Scripts\activate
python main.py
pause
