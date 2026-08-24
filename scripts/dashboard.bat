@echo off
REM Open the dashboard without starting the tracker.
setlocal
cd /d "%~dp0.."
python -m timesplit dashboard
endlocal
