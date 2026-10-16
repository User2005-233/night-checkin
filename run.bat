@echo off
rem Double-click to run auto check-in manually (logs go to checkin.log)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 99
)
.venv\Scripts\python.exe auto_checkin.py
echo.
echo Exit code %errorlevel%: 0=ok 1=failed(check log/Toast) 2=out of window 3=already signed 4=no task tonight 9=crashed 99=missing deps
pause
