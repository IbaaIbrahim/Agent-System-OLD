@echo off
REM Windows development script for Agent System
REM Alternative to Makefile for Windows users

setlocal enabledelayedexpansion

if "%~1"=="" goto help
if /I "%~1"=="help" goto help
if /I "%~1"=="api" goto api
if /I "%~1"=="stream" goto stream
if /I "%~1"=="orchestrator" goto orchestrator
if /I "%~1"=="workers" goto workers
if /I "%~1"=="archiver" goto archiver
if /I "%~1"=="ws" goto ws
if /I "%~1"=="live-session" goto live_session
if /I "%~1"=="auth-broker" goto auth_broker
if /I "%~1"=="frontend" goto frontend
if /I "%~1"=="migrate" goto migrate
if /I "%~1"=="test" goto test
if /I "%~1"=="test-unit" goto test_unit
if /I "%~1"=="test-int" goto test_int
if /I "%~1"=="lint" goto lint
if /I "%~1"=="format" goto format
if /I "%~1"=="typecheck" goto typecheck

echo Unknown command: %~1
goto help

:help
echo Agent System - Windows Development Script
echo.
echo Usage: dev.bat ^<command^>
echo.
echo Infrastructure:
echo   dev.bat up           - Start all Docker services
echo   dev.bat down         - Stop all Docker services
echo   dev.bat logs         - Tail logs from all services
echo   dev.bat clean        - Remove containers, volumes, and images
echo.
echo Development:
echo   dev.bat install      - Install Python and Node dependencies
echo   dev.bat migrate      - Run database migrations
echo.
echo Individual Services:
echo   dev.bat api          - Run API Gateway locally (port 8000)
echo   dev.bat stream       - Run Stream Edge locally (port 8001)
echo   dev.bat orchestrator - Run Orchestrator locally
echo   dev.bat workers      - Run Tool Workers locally
echo   dev.bat archiver     - Run Archiver locally
echo   dev.bat ws           - Run WebSocket Gateway locally (port 8002)
echo   dev.bat live-session - Run Live Session Manager locally
echo   dev.bat auth-broker  - Run Auth Broker locally
echo   dev.bat frontend     - Run Frontend locally (port 3000)
echo.
echo Testing:
echo   dev.bat test         - Run all tests
echo   dev.bat test-unit    - Run unit tests only
echo   dev.bat test-int     - Run integration tests
echo.
echo Code Quality:
echo   dev.bat lint         - Run linters
echo   dev.bat format       - Format code
echo   dev.bat typecheck    - Run type checking
goto end

:api
set PYTHONPATH=%CD%
uvicorn services.api-gateway.src.main:app --reload --port 8000 --host localhost
goto end

:stream
set PYTHONPATH=%CD%
uvicorn services.stream-edge.src.main:app --reload --port 8001 --host localhost
goto end

:orchestrator
set PYTHONPATH=%CD%
where watchfiles >nul 2>&1
if %errorlevel%==0 (
    watchfiles --ignore-paths .cursor "python -m services.orchestrator.src.main"
) else (
    python -m services.orchestrator.src.main
)
goto end

:workers
set PYTHONPATH=%CD%
where watchfiles >nul 2>&1
if %errorlevel%==0 (
    watchfiles "python -m services.tool-workers.src.main"
) else (
    python -m services.tool-workers.src.main
)
goto end

:archiver
set PYTHONPATH=%CD%
where watchfiles >nul 2>&1
if %errorlevel%==0 (
    watchfiles "python -m services.archiver.src.main"
) else (
    python -m services.archiver.src.main
)
goto end

:ws
set PYTHONPATH=%CD%
uvicorn services.websocket-gateway.src.main:app --reload --port 8002 --host localhost
goto end

:live_session
set PYTHONPATH=%CD%
where watchfiles >nul 2>&1
if %errorlevel%==0 (
    watchfiles "python -m services.live-session-manager.src.main"
) else (
    python -m services.live-session-manager.src.main
)
goto end

:auth_broker
cd services\auth-broker
python main.py
goto end

:frontend
cd frontend\apps\demo
npm run dev
goto end

:migrate
python -m alembic -c migrations\alembic.ini upgrade head
goto end

:test
pytest tests/ -v
goto end

:test_unit
pytest tests/unit/ -v
goto end

:test_int
pytest tests/integration/ -v
goto end

:lint
ruff check libs/ services/
cd frontend && npm run lint
goto end

:format
ruff format libs/ services/
cd frontend && npm run format
goto end

:typecheck
mypy libs/ services/ --ignore-missing-imports
goto end

:up
docker compose up -d
goto end

:down
docker compose down --rmi local
goto end

:logs
docker compose logs -f
goto end

:clean
docker compose down --volumes --rmi local
docker system prune -f
goto end

:end
endlocal
