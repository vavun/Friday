@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m friday.launcher %*
) else (
    python -m friday.launcher %*
)
