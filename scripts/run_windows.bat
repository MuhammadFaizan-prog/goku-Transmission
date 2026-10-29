@echo off
REM Goku Transmission - one-click launcher for Windows
cd /d "%~dp0\.."
if not exist .venv (
  echo [setup] creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip >nul
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python -m goku_transmission %*
pause
