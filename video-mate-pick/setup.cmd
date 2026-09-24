@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
echo Setup complete. Run start-services.cmd.
exit /b 0
:failed
echo Setup failed. Read the error above.
pause
exit /b 1
