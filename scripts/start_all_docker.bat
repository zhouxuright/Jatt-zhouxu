@echo off
REM =============================================================================
REM Legal Intelligent Assistance System - one-click full stack startup
REM Usage: double-click this file after rebooting (WSL2 first boot needs it),
REM        or run from a terminal: scripts\start_all_docker.bat
REM =============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0.."

echo [1/4] Starting Docker Desktop...
if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" (
    start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
) else (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
)

echo [2/4] Waiting for Docker engine (this can take 1-3 minutes on first boot)...
set /a tries=0
:wait_engine
docker ps >nul 2>&1
if %errorlevel%==0 goto engine_ready
set /a tries+=1
if %tries% geq 60 (
    echo ERROR: Docker engine did not start within 10 minutes.
    echo Check Docker Desktop is running, then re-run this script.
    pause
    exit /b 1
)
timeout /t 10 /nobreak >nul
goto wait_engine

:engine_ready
echo Docker engine is ready.

echo [3/4] Building and starting all services (backend x3, frontend, nginx,
echo        postgres, redis, milvus, etcd, minio, proxy_pool)...
docker compose --profile full up -d --build
if %errorlevel% neq 0 (
    echo ERROR: docker compose failed. Scroll up for details.
    pause
    exit /b 1
)

echo [4/4] Waiting for health checks (first boot downloads AI models, be patient)...
timeout /t 60 /nobreak >nul
docker compose --profile full ps

echo.
echo =============================================================================
echo All services started. Access points:
echo   Frontend (direct)   : http://localhost:3000
echo   API via Nginx       : http://localhost
echo   API docs (Swagger)  : http://localhost:8000/docs  (or backend replica ports)
echo   Health check        : http://localhost:3000/api/health  (via nginx) or backend /health
echo   Proxy pool API      : http://localhost:5010/count/  (IP proxy pool)
echo   Logs                : docker compose logs -f backend
echo =============================================================================
pause
