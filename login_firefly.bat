@echo off
setlocal
cd /d "%~dp0"
set "FIREFLY_LOGIN_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%FIREFLY_LOGIN_PYTHON%" (
  echo [ERRO] Python do Firefly nao encontrado em "%FIREFLY_LOGIN_PYTHON%".
  pause
  exit /b 1
)

"%FIREFLY_LOGIN_PYTHON%" "%~dp0scripts\first_login.py"
pause
