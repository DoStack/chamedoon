@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" login.py --server
echo.
pause
