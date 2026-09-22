@echo off
REM VOXORYL Setup Wizard (Windows)
setlocal
cd /d "%~dp0\.."

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%~dp0setup_voxoryl.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "%~dp0setup_voxoryl.py" %*
  exit /b %ERRORLEVEL%
)

echo Python 3.11+ not found. Install from https://www.python.org/downloads/
echo Then double-click this file again.
pause
exit /b 1
