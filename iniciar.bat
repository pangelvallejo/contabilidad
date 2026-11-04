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
if errorlevel 1 (
  echo No se pudieron instalar las librerias. Revise la conexion a internet y vuelva a intentarlo.
  pause
  exit /b 1
)
if not exist "%USERPROFILE%\Desktop\Contabilidad Angel Lecompte.lnk" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0crear_acceso_directo.ps1" >nul 2>&1
)
REM Sin ventana negra: el programa queda abierto en segundo plano y se cierra desde Otros -> Cerrar el programa
start "" "%~dp0.venv\Scripts\pythonw.exe" -m app
exit /b 0
