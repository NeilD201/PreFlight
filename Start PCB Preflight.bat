@echo off
rem PreFlight launcher for Windows. Creates the project .venv on first run,
rem installs dependencies, then starts the local web app.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is required - see SETUP.md & exit /b 1)
if not exist .venv\Scripts\python.exe (
  echo Creating .venv - this takes a minute on first run...
  python -m venv .venv || exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || exit /b 1
python -m pip install -q --target .deps -r requirements.txt || exit /b 1
python main.py --web
