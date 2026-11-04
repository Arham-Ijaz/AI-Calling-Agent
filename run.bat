@echo off
setlocal
title AI Calling Agent — Production Auto Launcher

echo =======================================================
echo         AI Calling Agent — Production Auto Launcher
echo =======================================================
echo.

:: 1. Locate Python
set "PY_CMD="
where python >nul 2>&1
if %errorlevel% equ 0 set "PY_CMD=python"
if not defined PY_CMD (
    where py >nul 2>&1
    if %errorlevel% equ 0 set "PY_CMD=py"
)

if not defined PY_CMD (
    echo [ERROR] Python was not found on your system!
    echo Please install Python 3.10+ from: https://www.python.org/
    echo Be sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [OK] Using Python:
%PY_CMD% --version
echo.

:: 2. Ensure .env exists
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] No .env file found. Creating .env from .env.example...
        copy /Y ".env.example" ".env" >nul
        echo [OK] Created .env configuration file.
    ) else (
        echo [WARNING] .env.example was not found.
    )
)

:: 3. Install / Verify dependencies from requirements.txt
echo [INFO] Verifying and installing required dependencies...
echo (Please wait while dependencies are checked...)
echo.
%PY_CMD% -m pip install -r requirements.txt --disable-pip-version-check
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Standard pip install encountered a permission issue. Retrying with --user...
    %PY_CMD% -m pip install --user -r requirements.txt --disable-pip-version-check
)
echo.
echo [OK] All dependencies are ready!
echo.

:: 4. Verify Google Gemini API Key
%PY_CMD% -c "import os; from dotenv import load_dotenv; load_dotenv(); exit(0 if os.getenv('GEMINI_API_KEY') and os.getenv('GEMINI_API_KEY') != 'your_primary_gemini_api_key_here' else 1)" >nul 2>&1
if errorlevel 1 (
    echo ============================================================
    echo  [SETUP] Google Gemini API Key required!
    echo  Get your free key: https://aistudio.google.com/app/apikey
    echo ============================================================
    set /p "USER_API_KEY=Paste Gemini API Key here (or press Enter to configure in .env): "
    if defined USER_API_KEY (
        powershell -Command "(Get-Content .env) -replace 'GEMINI_API_KEY=.*', ('GEMINI_API_KEY=' + $env:USER_API_KEY) | Set-Content .env"
        echo [OK] API Key saved to .env!
    ) else (
        echo [NOTE] Please edit .env file and paste your GEMINI_API_KEY before calling.
    )
    echo.
)

:: 5. Launch Server
echo =======================================================
echo   Starting AI Calling Agent Production Server...
echo   Web Dashboard:   http://127.0.0.1:8000
echo   WebSocket Voice: ws://127.0.0.1:8000/ws/ai-call
echo   Telephony Voice: http://127.0.0.1:8000/api/telephony/voice
echo   To stop server:  Press CTRL+C
echo =======================================================
echo.

%PY_CMD% app.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] The application stopped with error code %errorlevel%.
    pause
)
