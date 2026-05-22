@echo off
taskkill /F /IM python.exe 2>nul
rd /s /q "src\__pycache__" 2>nul
rd /s /q "ui\__pycache__" 2>nul
rd /s /q "ui\pages\__pycache__" 2>nul
cd ui
..\\.venv\Scripts\streamlit run app.py
