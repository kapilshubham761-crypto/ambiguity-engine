@echo off
title Ambiguity Engine — Visualizer
color 0B

echo.
echo  ============================================
echo   VISUALIZER  ^|  port 8502
echo  ============================================
echo.

:: Start visualizer
start "" cmd /k "cd /d "%~dp0" && .venv\Scripts\streamlit run visualizer.py --server.port 8502 --server.headless true"

echo  Waiting for visualizer to come online...
echo.

set /a tries=0
:POLL
set /a tries+=1
if %tries% GTR 20 (
    echo  [FAILED] Did not start after 40 seconds.
    echo  Check the terminal window for errors.
    pause
    exit /b 1
)

curl -s --max-time 2 http://localhost:8502/_stcore/health >nul 2>&1
if %errorlevel% EQU 0 goto ONLINE

set /a elapsed=%tries%*2
echo  [%tries%/20] Starting... (%elapsed%s elapsed)
timeout /t 2 /nobreak >nul
goto POLL

:ONLINE
echo.
echo  ============================================
echo   VISUALIZER ONLINE  ^<^<  http://localhost:8502
echo  ============================================
echo.
start "" "http://localhost:8502"
echo  Browser opened.
echo  Close this window to keep visualizer running.
echo.
pause
