@echo off
REM Stop TimeSplit starting at logon. Your recorded time is not touched.
setlocal
cd /d "%~dp0.."
python -m timesplit uninstall-autostart
echo.
pause
endlocal
