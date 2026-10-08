@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Follow installation steps in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m proctor --perf auto %*
if errorlevel 1 pause
