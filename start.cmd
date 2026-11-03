@echo off
rem Double-click to start TUM Lecture Studio on Windows.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
pause
