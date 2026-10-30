@echo off
rem Created by Jobs. Build Jobs Codex Account Switcher EXE directory and ZIP on Windows.
rem Only this tool's dist is cleared. The inner builder confirms before any mutation.
setlocal DisableDelayedExpansion
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
echo Jobs Codex Account Switcher - Windows native build
echo Removes only this tool's old dist packages after Enter confirmation.
echo Missing required dependencies: Enter installs; any input, including spaces, cancels.
echo Healthy dependencies are reused without upgrade. No cross-platform compilation.
echo Output: dist/YYYY.MM.DD HH-mm-ss, with EXE and complete runtime dependencies in ZIP.
echo Creates first-level EXE / ZIP shortcuts, opens output, then launches this build.
echo Log: JobsCodexAccountSwitcher-build.log in the system temporary directory.
if /i "%~1"=="--help" goto help
if /i "%~1"=="-h" goto help
py -3 -c "import sys,venv,pathlib; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 goto try_python
set "JOBS_ACCOUNT_PYTHON=py"
set "JOBS_ACCOUNT_PYTHON_ARGS=-3"
goto build
:try_python
python -c "import sys,venv,pathlib; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 goto no_python
set "JOBS_ACCOUNT_PYTHON=python"
set "JOBS_ACCOUNT_PYTHON_ARGS="
:build
set "PYTHONDONTWRITEBYTECODE=1"
"%JOBS_ACCOUNT_PYTHON%" %JOBS_ACCOUNT_PYTHON_ARGS% "%~dp0JobsCodexAccountSwitcher\scripts\build.py" %*
if errorlevel 1 goto failed
exit /b 0
:help
echo Usage: double-click or run in a terminal. --help only displays this description.
echo Builds on this Windows computer; the inner builder owns the confirmation prompt.
exit /b 0
:no_python
echo Install Python 3.11+ from https://www.python.org/downloads/ and enable PATH or Python Launcher.
:failed
echo Build stopped. No old application will be launched. See the error and build log.
pause
exit /b 1
