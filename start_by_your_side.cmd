@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Follow README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "scripts\run_by_your_side.py" --open %*
if errorlevel 1 (
    pause
    exit /b 1
)
exit /b 0
