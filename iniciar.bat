@echo off
REM Contabilidad Angel Lecompte S.A.S. - doble clic para abrir el programa
title Contabilidad Angel Lecompte
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Preparando el programa por primera vez, espere un momento...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo No se encontro Python. Instalelo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH".
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python.exe -m app
pause
