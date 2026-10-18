@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. See docs/photo_booth/OTHER_LAPTOP_TEST.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "scripts\run_photo_booth.py" --trained %*
if errorlevel 1 (
    pause
    exit /b 1
)
