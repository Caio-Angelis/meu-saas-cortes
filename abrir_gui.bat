@echo off
cd /d "%~dp0"
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765/"
python web_main.py
if errorlevel 1 (
  py web_main.py
)
if errorlevel 1 (
  echo Nao foi possivel abrir o Studio Cortes. Confira se o Python esta instalado e no PATH.
  pause
)
