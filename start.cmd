@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0novelsim.ps1" start --open-browser %*
if errorlevel 1 pause
