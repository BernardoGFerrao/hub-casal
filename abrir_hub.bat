@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File start_hub.ps1
pause
