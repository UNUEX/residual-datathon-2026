@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local_ai.ps1" %*
if errorlevel 1 pause