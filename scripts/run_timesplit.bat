@echo off
REM Start TimeSplit in the background (no console window).
REM Double-click this, or let the scheduled task run it at logon.
setlocal
cd /d "%~dp0.."
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw -m timesplit run
) else (
  start "" python -m timesplit run
)
endlocal
