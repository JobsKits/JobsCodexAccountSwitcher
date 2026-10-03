@echo off
rem Created by Jobs. Build EXE/ZIP on Windows; never switches Codex auth.
setlocal
chcp 65001 >nul
echo Jobs Codex Account Switcher - Windows build
echo Removes this tool's old dist packages. Missing dependencies require Enter to install; any input cancels.
echo Creates dist/YYYY.MM.DD HH-mm-ss, publishes shortcuts, opens output and launches the new EXE.
echo Log: JobsCodexAccountSwitcher-build.log in the system temp directory.
py -3 -c "import sys,venv,pathlib; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 goto no_python
py -3 "%~dp0JobsCodexAccountSwitcher\scripts\build.py"
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Install Python 3.11+ with Python Launcher: https://www.python.org/downloads/
:failed
echo Build failed. No old package will be launched.
pause
exit /b 1
