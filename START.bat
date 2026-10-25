@echo off
REM ATLAS launcher (Windows) — keep this black window open while you use ATLAS
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv || (echo Python 3.10+ required: https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo Starting ATLAS... leave this window open.
echo When ready, open Edge or Chrome to:  http://127.0.0.1:8765
echo.
python app.py %*
pause
