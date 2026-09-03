@echo off
REM ============================================================
REM ApexTrader AI — Backend Server Startup Script
REM Python: 64-bit Python 3.11.9 (required by MetaTrader5)
REM ============================================================

SET PYTHON=C:\Users\localadmin\AppData\Local\Programs\Python\Python311\python.exe
SET BACKEND_DIR=%~dp0backend

echo.
echo  ============================================================
echo   ApexTrader AI - Production MT5 Trading Bot
echo  ============================================================
echo   Backend:   http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo   Dashboard: http://localhost:8000/frontend/index.html
echo  ============================================================
echo.

cd /d "%BACKEND_DIR%"
"%PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

echo.
echo [Server stopped]
pause
