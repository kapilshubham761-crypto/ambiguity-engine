@echo off
taskkill /F /IM python.exe 2>nul
rd /s /q "src\__pycache__" 2>nul
rd /s /q "ui\__pycache__" 2>nul
rd /s /q "ui\_pages\__pycache__" 2>nul

:: Suppress Streamlit email prompt
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"

cd ui
..\.venv\Scripts\streamlit run app.py
