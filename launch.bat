@echo off
cd /d %~dp0

REM Si estás detrás de proxy/certificado corporativo y falla SSL con huggingface, descomenta:
REM set AIYA_HF_SSL_VERIFY=false

python -m venv venv
if errorlevel 1 (
  echo [ERROR] No se pudo crear el entorno virtual.
  exit /b 1
)

venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Fallo instalando requirements.
  exit /b 1
)

venv\Scripts\python.exe core/setup_generate.py
if errorlevel 1 (
  echo [ERROR] Fallo la descarga de modelos en core/setup_generate.py.
  echo [ERROR] Revisa conectividad/SSL hacia huggingface.co antes de arrancar el bot.
  exit /b 1
)

venv\Scripts\python.exe aiya.py
