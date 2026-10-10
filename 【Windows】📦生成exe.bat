@echo off
rem Created by Jobs. Build EXE, standalone Token launcher and ZIP on Windows; never switches Codex auth.
setlocal
chcp 65001 >nul
echo Jobs Codex Account Switcher and Token Widget - Windows build
echo Removes this tool's old dist packages. Missing dependencies require Enter to install; any input cancels.
echo Creates dist/YYYY.MM.DD HH-mm-ss, publishes shortcuts, opens output and launches the new Token widget.
echo Includes a standalone Token launcher. Keep its VBS beside the EXE when extracting the ZIP.
echo Requires system PowerShell and Windows Script Host. Building does not install Codex hooks.
echo Log: JobsCodexAccountSwitcher-build.log in the system temp directory.
py -3 -c "import sys,venv,pathlib; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 goto no_python
set "PYTHONDONTWRITEBYTECODE=1"
py -3 "%~dp0JobsCodexAccountSwitcher\scripts\build.py"
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Install Python 3.11+ with Python Launcher: https://www.python.org/downloads/
:failed
echo Build failed. No old package will be launched.
pause
exit /b 1
