@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 "%~dp0scripts\start_local.py" %*
) else (
  python "%~dp0scripts\start_local.py" %*
)

exit /b %errorlevel%
