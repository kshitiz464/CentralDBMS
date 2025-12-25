@echo off
echo Starting Chrome in Remote Debugging Mode...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-debug-profile"
echo Chrome launched. You can now close this window if you want, or keep it open.
pause
