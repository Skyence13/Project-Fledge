@echo off
setlocal
cd /d "%~dp0"
start "" /b pythonw.exe "%~dp0local_monitor.py"
