@echo off
setlocal

cd /d "%~dp0"

set "PY_EXE=C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe"

if not exist "%PY_EXE%" (
  echo Python not found at %PY_EXE%
  echo Edit run.bat and update PY_EXE path.
  pause
  exit /b 1
)

echo Installing/updating dependencies...
"%PY_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

set "PYTHONPATH=src"

echo Starting Pocket Option Bot...
"%PY_EXE%" src\main.py

endlocal
