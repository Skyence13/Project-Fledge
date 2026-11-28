@echo off
setlocal
python.exe -B "%~dp0install_fledge_coordinator.py"
set "fledge_result=%errorlevel%"
if not "%fledge_result%"=="0" pause
exit /b %fledge_result%
