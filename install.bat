@echo off
cd /d %~dp0

echo ===========================================
echo    Central Management System Installer
echo ===========================================

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11+ and add it to PATH.
    pause
    exit /b
)

:: Handle existing venv (Fix for copied venvs)
if exist venv (
    echo.
    echo [WARNING] Existing 'venv' folder found.
    echo If you copied this folder from another PC, the venv is likely broken.
    echo.
    set /p REINSTALL="Do you want to delete the old venv and reinstall? (Recommended for new PCs) [Y/N]: "
    
    REM Check variable, strictly.
) else (
    set REINSTALL=Y
)

if /i "%REINSTALL%"=="Y" (
    if exist venv (
        echo Deleting old venv...
        rmdir /s /q venv
    )
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating venv...
call venv\Scripts\activate

echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt

echo Installing Playwright Browsers...
python -m playwright install chromium

echo.
echo ===========================================
echo    Installation Complete!
echo    You can now run 'run_server.bat'.
echo ===========================================
pause
