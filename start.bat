@echo off
REM Double-click this. It starts the local site and opens a browser.
REM Close the window (or press Ctrl+C) to stop it.
cd /d "%~dp0"
python fb.py serve
if errorlevel 1 (
  echo.
  echo The server stopped with an error. If it says a module is missing, run:
  echo     pip install -r requirements.txt
  pause
)
