@echo off
REM One-time setup: install what TimeSplit needs, then run the wizard.
setlocal
cd /d "%~dp0.."
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements-win.txt
python -m pip install -e .
echo.
python -m timesplit wizard
echo.
pause
endlocal
