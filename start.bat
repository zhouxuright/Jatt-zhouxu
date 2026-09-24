@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: =============================================================================
:: Legal Intelligent Assistance System - Windows Startup Script
:: =============================================================================
:: This script checks prerequisites, installs dependencies, initializes the
:: database, loads seed data, and starts both backend and frontend servers.
::
:: Usage:
::   start.bat              # Full startup (check + install + init + run)
::   start.bat --skip-check # Skip prerequisite checks
::   start.bat --backend    # Start only the backend
::   start.bat --frontend   # Start only the frontend
:: =============================================================================

title Legal Intelligent Assistance System

echo.
echo ============================================================
echo   Legal Intelligent Assistance System - Startup Script
echo ============================================================
echo.

:: ---------------------------------------------------------------------------
:: Configuration
:: ---------------------------------------------------------------------------
set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "PYTHON_CMD=python"
set "NODE_CMD=node"
set "NPM_CMD=npm"

:: ---------------------------------------------------------------------------
:: Parse arguments
:: ---------------------------------------------------------------------------
set SKIP_CHECK=0
set BACKEND_ONLY=0
set FRONTEND_ONLY=0

:parse_args
if "%~1"=="" goto :check_prerequisites
if "%~1"=="--skip-check" (
    set SKIP_CHECK=1
    shift
    goto :parse_args
)
if "%~1"=="--backend" (
    set BACKEND_ONLY=1
    shift
    goto :parse_args
)
if "%~1"=="--frontend" (
    set FRONTEND_ONLY=1
    shift
    goto :parse_args
)
shift
goto :parse_args

:: ---------------------------------------------------------------------------
:: Check prerequisites
:: ---------------------------------------------------------------------------
:check_prerequisites
if %SKIP_CHECK%==1 goto :setup_backend

echo [1/6] Checking prerequisites...

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo   [OK] %PYTHON_VERSION%

:: Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo         Please install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version 2^>^&1') do set NODE_VERSION=%%i
echo   [OK] Node.js %NODE_VERSION%

:: Check npm
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm is not installed or not in PATH.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('npm --version 2^>^&1') do set NPM_VERSION=%%i
echo   [OK] npm %NPM_VERSION%

:: Check PostgreSQL (optional warning)
where psql >nul 2>&1
if %errorlevel% neq 0 (
    echo   [WARN] PostgreSQL client not found. Database will not be auto-initialized.
    echo          Please ensure PostgreSQL is running and .env is configured.
) else (
    echo   [OK] PostgreSQL client found
)

:: ---------------------------------------------------------------------------
:: Setup Backend
:: ---------------------------------------------------------------------------
:setup_backend
if %FRONTEND_ONLY%==1 goto :setup_frontend

echo.
echo [2/6] Setting up backend environment...

cd /d "%BACKEND_DIR%"

:: Create .env from .env.example if not exists
if not exist ".env" (
    if exist ".env.example" (
        echo   Copying .env.example to .env ...
        copy .env.example .env >nul
        echo   [INFO] Created .env from .env.example. Please edit .env with your settings.
    ) else (
        echo   [WARN] .env.example not found. Creating default .env ...
        (
            echo APP_ENV=development
            echo DEBUG=true
            echo SECRET_KEY=dev-secret-change-in-production
            echo DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/legal_assistant
            echo REDIS_URL=redis://localhost:6379/0
            echo LLM_PROVIDER=deepseek
            echo DEEPSEEK_API_KEY=sk-your-deepseek-api-key
            echo JWT_SECRET_KEY=dev-jwt-secret-change-in-production
            echo LOG_LEVEL=DEBUG
        ) > .env
        echo   [INFO] Created default .env. Please edit with your actual settings.
    )
)

:: Create virtual environment if not exists
if not exist "venv\" (
    echo   Creating Python virtual environment...
    python -m venv venv
    echo   [OK] Virtual environment created
)

:: Activate virtual environment and install dependencies
echo   Installing Python dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python dependencies.
    pause
    exit /b 1
)
echo   [OK] Python dependencies installed

:: ---------------------------------------------------------------------------
:: Initialize Database
:: ---------------------------------------------------------------------------
:init_database
if %FRONTEND_ONLY%==1 goto :setup_frontend

echo.
echo [3/6] Initializing database...

:: Check if PostgreSQL is accessible
python -c "import asyncpg; import asyncio; asyncio.run(asyncpg.connect('postgresql://postgres:postgres@localhost:5432/postgres'))" 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] Cannot connect to PostgreSQL. Make sure PostgreSQL is running.
    echo          Skipping database initialization.
    echo          To manually initialize, run: python -m app.rag.seed_data
    goto :setup_frontend
)

:: Create database if not exists
python -c "import asyncpg; import asyncio; async def create_db(): conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/postgres'); await conn.execute('CREATE DATABASE legal_assistant'); await conn.close(); asyncio.run(create_db())" 2>nul
echo   [OK] Database ready

:: Run Alembic migrations (if alembic is configured)
if exist "alembic.ini" (
    echo   Running database migrations...
    alembic upgrade head
    echo   [OK] Migrations applied
) else (
    echo   [INFO] No alembic.ini found. Skipping migrations.
    echo          Tables will be created automatically on first run.
)

:: ---------------------------------------------------------------------------
:: Load Seed Data
:: ---------------------------------------------------------------------------
:load_seed_data
echo.
echo [4/6] Loading seed data...

python -c "from app.rag.seed_data import load_seed_data; load_seed_data()" 2>nul
if %errorlevel% neq 0 (
    echo   [WARN] Seed data loading encountered issues (may be normal if vector store is not configured).
    echo          The system will still start but may have limited legal knowledge.
) else (
    echo   [OK] Seed data loaded
)

:: ---------------------------------------------------------------------------
:: Setup Frontend
:: ---------------------------------------------------------------------------
:setup_frontend
if %BACKEND_ONLY%==1 goto :start_servers

echo.
echo [5/6] Setting up frontend dependencies...

cd /d "%FRONTEND_DIR%"

:: Install npm dependencies
if not exist "node_modules\" (
    echo   Installing Node.js dependencies...
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install frontend dependencies.
        pause
        exit /b 1
    )
    echo   [OK] Frontend dependencies installed
) else (
    echo   [OK] node_modules already exists, skipping npm install
)

:: ---------------------------------------------------------------------------
:: Start Servers
:: ---------------------------------------------------------------------------
:start_servers
echo.
echo [6/6] Starting servers...

if %FRONTEND_ONLY%==1 goto :start_frontend_only
if %BACKEND_ONLY%==1 goto :start_backend_only

:: Start both servers
echo   Starting backend server (http://localhost:8000)...
cd /d "%BACKEND_DIR%"
start "Legal Backend" cmd /k "call venv\Scripts\activate.bat && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 /nobreak >nul

echo   Starting frontend server (http://localhost:3000)...
cd /d "%FRONTEND_DIR%"
start "Legal Frontend" cmd /k "npm run dev"

timeout /t 5 /nobreak >nul

goto :startup_complete

:start_backend_only
echo   Starting backend server (http://localhost:8000)...
cd /d "%BACKEND_DIR%"
start "Legal Backend" cmd /k "call venv\Scripts\activate.bat && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
goto :startup_complete

:start_frontend_only
echo   Starting frontend server (http://localhost:3000)...
cd /d "%FRONTEND_DIR%"
start "Legal Frontend" cmd /k "npm run dev"
goto :startup_complete

:: ---------------------------------------------------------------------------
:: Startup Complete
:: ---------------------------------------------------------------------------
:startup_complete
echo.
echo ============================================================
echo   Startup Complete!
echo ============================================================
echo.
echo   Backend API:  http://localhost:8000
echo   API Docs:     http://localhost:8000/docs
echo   Frontend:     http://localhost:3000
echo   Health Check: http://localhost:8000/health
echo.
echo   Press any key to close this window...
pause >nul
endlocal