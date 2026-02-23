@echo off
setlocal
cd /d "%~dp0"

echo ====================================================
echo   🎥 Quran Video Generator - Full Backend Starter
echo ====================================================
echo.

:: 1. Check Python installation
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERR] Python is not installed or not in PATH.
    pause
    exit /b
)

:: 2. Setup Virtual Environment
echo [2/5] Checking Environment...
if not exist .venv (
    echo [INF] Creating virtual environment...
    python -m venv .venv
)

:: 3. Install Dependencies
echo [3/5] Syncing Requirements...
.venv\Scripts\pip.exe install -r requirements.txt

:: 4. Start Celery Worker & Beat in NEW WINDOWS
echo [4/5] Launching Celery Worker...
start "Celery Worker" cmd /k ".venv\Scripts\python.exe -m celery -A app.celery_app worker --loglevel=info -P solo"

echo [INF] Launching Celery Beat (Scheduler)...
start "Celery Beat" cmd /k ".venv\Scripts\python.exe -m celery -A app.celery_app beat --loglevel=info"

:: 5. Start Backend Server
echo [5/5] Launching API Server...
echo.
echo [OK] Server will be available at http://localhost:8000
echo [OK] Celery Worker/Beat are running in separate windows.
echo [!] Make sure Redis server is running!
echo.

.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

pause
