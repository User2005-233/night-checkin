@echo off
rem Setup: create .venv with Python 3.14 and install dependencies.
rem Python 3.15 is NOT supported: onnxruntime has no cp315 wheel, so ddddocr cannot install.
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\16156\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python 3.14 not found at: %PYTHON_EXE%
    echo [ERROR] Python 3.15 cannot run this project because onnxruntime has no cp315 wheel.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv with Python 3.14 ...
    "%PYTHON_EXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        pause
        exit /b 1
    )
) else (
    echo .venv already exists, skip creation.
)

set "VENV_PY=.venv\Scripts\python.exe"
"%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,14) else 1)"
if errorlevel 1 (
    echo [ERROR] .venv is not Python 3.14. Delete .venv and rerun this script.
    pause
    exit /b 1
)

rem Keep pip temp/cache on the project drive: C: may be nearly full.
set "PIP_TMP=%~dp0.pip-tmp"
set "PIP_CACHE=%~dp0.pip-cache"
if not exist "%PIP_TMP%" mkdir "%PIP_TMP%"
if not exist "%PIP_CACHE%" mkdir "%PIP_CACHE%"
set "TEMP=%PIP_TMP%"
set "TMP=%PIP_TMP%"
set "PIP_CACHE_DIR=%PIP_CACHE%"

echo Installing dependencies (Tsinghua mirror, proxy bypassed) ...
set "http_proxy="
set "https_proxy="
set "HTTP_PROXY="
set "HTTPS_PROXY="
"%VENV_PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Retry with a mirror:
    echo   %VENV_PY% -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    pause
    exit /b 1
)

echo.
echo Verifying ...
"%VENV_PY%" -c "import requests, PIL, ddddocr, windows_toasts; print('deps ok')"
echo.
echo Done. Double-click run.bat for a manual check-in run.
pause