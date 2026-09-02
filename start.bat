@echo off
setlocal EnableDelayedExpansion

rem ===========================================================================
rem  F1 Race Analysis Engine - start everything
rem
rem  Brings up the four pieces in dependency order: database and cache, then
rem  the API, then the UI. Each step waits for the previous one to be genuinely
rem  ready rather than sleeping a fixed time, because a container reporting
rem  "started" is not the same as accepting connections.
rem
rem  Usage:
rem    start.bat            start everything
rem    start.bat --stop     stop the API and UI (leaves containers running)
rem    start.bat --down     stop everything including containers
rem ===========================================================================

cd /d "%~dp0"

set "VENV=.venv\Scripts\python.exe"
set "COMPOSE=docker compose -f docker/docker-compose.yml"
set "API_PORT=8000"
set "UI_PORT=3000"

if /i "%~1"=="--stop" goto :stop_apps
if /i "%~1"=="--down" goto :stop_all
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h" goto :usage

echo.
echo   F1 Race Analysis Engine
echo   =======================
echo.

rem --- prerequisites ---------------------------------------------------------
if not exist "%VENV%" (
    echo   [x] No virtual environment at %VENV%
    echo.
    echo       py -3.12 -m venv .venv
    echo       .venv\Scripts\python.exe -m pip install -e "backend[dev]"
    echo.
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo   [x] Frontend dependencies are not installed.
    echo.
    echo       cd frontend ^&^& npm install
    echo.
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo   [x] Docker is not running. Start Docker Desktop and try again.
    exit /b 1
)

rem --- free the ports --------------------------------------------------------
rem A previous run left running will hold these. Worse, a stale Next.js server
rem serves an old build whose CSS hash no longer exists, so the page renders
rem completely unstyled - a confusing failure worth preventing outright.
call :free_port %API_PORT% "API"
call :free_port %UI_PORT% "UI"

rem --- database and cache ----------------------------------------------------
echo   [1/4] Starting database and cache...
%COMPOSE% up -d >nul 2>&1
if errorlevel 1 (
    echo         failed. Try: %COMPOSE% up
    exit /b 1
)

echo   [2/4] Waiting for the database...
set /a tries=0
:wait_db
docker exec f1x-db pg_isready -U f1x -d f1x >nul 2>&1
if not errorlevel 1 goto :db_ready
set /a tries+=1
if !tries! GEQ 60 (
    echo         database did not become ready in 60s.
    echo         Check: docker logs f1x-db
    exit /b 1
)
ping -n 2 127.0.0.1 >nul 2>&1
goto :wait_db
:db_ready

rem A migration that has not been applied makes every API call 404 with a
rem confusing message, so apply it here rather than leaving it to be discovered.
pushd backend
..\%VENV% -m alembic upgrade head >nul 2>&1
popd

rem --- api -------------------------------------------------------------------
echo   [3/4] Starting API on port %API_PORT%...
start "f1x API" /min cmd /c "%VENV% -m uvicorn f1x.api.app:app --host 127.0.0.1 --port %API_PORT%"

set /a tries=0
:wait_api
curl -s -o nul http://127.0.0.1:%API_PORT%/health 2>nul
if not errorlevel 1 goto :api_ready
set /a tries+=1
if !tries! GEQ 40 (
    echo         API did not start. Run it directly to see why:
    echo         %VENV% -m f1x.cli api serve
    exit /b 1
)
ping -n 2 127.0.0.1 >nul 2>&1
goto :wait_api
:api_ready

rem --- ui --------------------------------------------------------------------
rem Dev mode by default: it compiles on demand, so an edit is visible without a
rem rebuild. Pass --prod for the optimised build.
echo   [4/4] Starting UI on port %UI_PORT%...
if /i "%~1"=="--prod" (
    pushd frontend
    call npm run build >nul 2>&1
    if errorlevel 1 (
        echo         frontend build failed. Run: cd frontend ^&^& npm run build
        popd
        exit /b 1
    )
    start "f1x UI" /min cmd /c "npm run start -- --port %UI_PORT%"
    popd
) else (
    pushd frontend
    start "f1x UI" /min cmd /c "npm run dev -- --port %UI_PORT%"
    popd
)

set /a tries=0
:wait_ui
curl -s -o nul http://127.0.0.1:%UI_PORT% 2>nul
if not errorlevel 1 goto :ui_ready
set /a tries+=1
if !tries! GEQ 60 (
    echo         UI did not start. Run it directly to see why:
    echo         cd frontend ^&^& npm run dev
    exit /b 1
)
ping -n 2 127.0.0.1 >nul 2>&1
goto :wait_ui
:ui_ready

rem --- report ----------------------------------------------------------------
echo.
echo   Running:
echo     UI       http://localhost:%UI_PORT%
echo     API      http://127.0.0.1:%API_PORT%/docs
echo     Health   http://127.0.0.1:%API_PORT%/health
echo.
for /f "tokens=*" %%i in ('docker exec f1x-db psql -U f1x -d f1x -tAc "SELECT count(*) FROM core.sessions" 2^>nul') do set SESSIONS=%%i
if defined SESSIONS (
    if "!SESSIONS!"=="0" (
        echo   No sessions ingested yet:
        echo     %VENV% -m f1x.cli ingest backfill 2023 --last-round 22 --no-telemetry
        echo     %VENV% -m f1x.cli transform all
        echo     %VENV% -m f1x.cli analyse all
        echo.
    ) else (
        echo   !SESSIONS! sessions loaded.
        echo.
    )
)
echo   Stop with:  start.bat --stop      ^(leaves containers up^)
echo               start.bat --down      ^(stops everything^)
echo.

start "" http://localhost:%UI_PORT%
exit /b 0


rem ===========================================================================
rem  helpers
rem ===========================================================================

:free_port
rem Kill whatever holds a port, so a stale process cannot serve a stale build.
set "PORT=%~1"
set "LABEL=%~2"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    if not "%%p"=="0" (
        echo   [-] Stopping the previous %LABEL% on port %PORT% ^(pid %%p^)
        taskkill /F /PID %%p >nul 2>&1
    )
)
exit /b 0

:stop_apps
echo.
echo   Stopping the API and UI...
call :free_port %API_PORT% "API"
call :free_port %UI_PORT% "UI"
echo   Containers are still running. Use --down to stop those too.
echo.
exit /b 0

:stop_all
echo.
echo   Stopping the API and UI...
call :free_port %API_PORT% "API"
call :free_port %UI_PORT% "UI"
echo   Stopping containers...
%COMPOSE% down >nul 2>&1
echo   Everything stopped. Ingested data is preserved in the Docker volume.
echo.
exit /b 0

:usage
echo.
echo   start.bat            start database, cache, API and UI
echo   start.bat --prod     same, but build and serve the optimised UI
echo   start.bat --stop     stop the API and UI, leave containers running
echo   start.bat --down     stop everything
echo.
exit /b 0
