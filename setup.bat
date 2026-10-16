@echo off
rem Setup: create .venv, install dependencies from requirements.txt
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ with "Add to PATH" checked.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        pause
        exit /b 1
    )
) else (
    echo .venv already exists, skip creation.
)
echo Installing dependencies ...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. If it was a network issue, retry with a mirror:
    echo   .venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    pause
    exit /b 1
)
echo.
echo Verifying ...
.venv\Scripts\python.exe -c "import requests,PIL,ddddocr; print('deps ok')"
echo.
echo Done. Double-click run.bat for a manual check-in run.
pause
