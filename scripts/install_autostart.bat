@echo off
REM Register TimeSplit to start when you log in.
setlocal
cd /d "%~dp0.."
python -m timesplit install-autostart
echo.
pause
endlocal
