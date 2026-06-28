@echo off
cd /d "%~dp0"
pythonw gui.py
if errorlevel 1 (
  pyw gui.py
)
if errorlevel 1 (
  echo Nao foi possivel abrir o programa. Confira se o Python esta instalado e no PATH.
  pause
)
