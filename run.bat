@echo off
cd /d "%~dp0ui"
python -m streamlit run app.py --server.port 8501 --server.headless true
