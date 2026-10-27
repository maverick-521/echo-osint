@echo off
REM ECHO launcher (Windows).
cd /d "%~dp0"
if not exist "requirements.txt" (
  echo ERROR: requirements.txt missing. Run this from the unzipped ECHO folder on your Desktop.
  echo Current folder: %CD%
  pause
  exit /b 1
)
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3 not found. Install from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First run: bringing ECHO online...
  %PY% -m venv .venv || (echo Failed to create .venv & pause & exit /b 1)
)
echo Checking packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "%CD%\requirements.txt" || (echo Failed to install packages & pause & exit /b 1)
echo.
echo Starting ECHO at http://127.0.0.1:8765 ...
".venv\Scripts\python.exe" app\server.py
pause
