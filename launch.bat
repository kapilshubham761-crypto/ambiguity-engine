@echo off
title Ambiguity Engine
color 0A

echo.
echo  ============================================
echo   AMBIGUITY ENGINE - LAUNCHER
echo  ============================================
echo.

:: Kill any existing instance
echo  [1/5] Stopping old instances...
taskkill /F /IM python.exe 2>nul && echo        Killed. || echo        Nothing running.
timeout /t 1 /nobreak >nul

:: Wipe stale caches
echo  [2/5] Clearing caches...
rd /s /q "%~dp0src\__pycache__" 2>nul
rd /s /q "%~dp0ui\__pycache__" 2>nul
rd /s /q "%~dp0ui\_pages\__pycache__" 2>nul
echo        Done.

:: Suppress Streamlit email prompt
echo  [3/5] Configuring Streamlit...
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
echo        Done.

:: Start overlay (silent — no console window)
echo  [4/5] Starting stats overlay...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0overlay.py"
echo        Done.

:: Open tracker HTML
start "" "%~dp0ambiguity-engine-tracker.html"

:: Start Streamlit
echo  [5/5] Starting engine...
set PYTHONDONTWRITEBYTECODE=1
start "" cmd /k "cd /d "%~dp0ui" && ..\.venv\Scripts\streamlit run app.py --server.runOnSave true --server.port 8501 --server.headless true"

echo.
echo  Waiting for engine to come online...
echo  (polling every 2 seconds, timeout 60s)
echo.

:: Poll until healthy or timeout
set /a tries=0
:POLL
set /a tries+=1
if %tries% GTR 30 (
    echo  [FAILED] Engine did not start after 60 seconds.
    echo  Check the Streamlit terminal window for errors.
    pause
    exit /b 1
)

curl -s --max-time 2 http://localhost:8501/_stcore/health >nul 2>&1
if %errorlevel% EQU 0 goto ONLINE

set /a elapsed=%tries%*2
echo  [%tries%/30] Still starting... (%elapsed%s elapsed)
timeout /t 2 /nobreak >nul
goto POLL

:ONLINE
echo.
echo  ============================================
echo   ENGINE IS ONLINE  << http://localhost:8501
echo  ============================================
echo.
start "" "http://localhost:8501"
echo  Browser opened.  Overlay running.
echo  Close this window to keep everything running.
echo.
pause
