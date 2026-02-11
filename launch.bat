@echo off
cd /d %~dp0
setlocal

REM If you are behind a corporate proxy/certificate and SSL fails with huggingface,
REM uncomment the following line:
REM set AIYA_HF_SSL_VERIFY=false

REM Single source of truth for the virtual environment path.
set "VENV_DIR=%~dp0venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

if not exist "%VENV_PY%" (
  echo [INFO] Creating virtual environment at "%VENV_DIR%"
  python -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    exit /b 1
  )
)

"%VENV_PIP%" install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed installing requirements.
  exit /b 1
)

"%VENV_PY%" core/setup_generate.py
if errorlevel 1 (
  echo [ERROR] Model setup failed in core/setup_generate.py.
  echo [ERROR] Check connectivity/SSL to huggingface.co before starting the bot.
  exit /b 1
)

"%VENV_PY%" aiya.py
endlocal
