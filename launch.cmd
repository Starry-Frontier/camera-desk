@echo off
setlocal
cd /d "%~dp0"
if exist "dist\CameraDesk\CameraDesk.exe" (
  start "" "dist\CameraDesk\CameraDesk.exe" %*
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "main.py" %*
  exit /b
)
echo Camera Desk is not built yet.
echo Use the portable package: unzip the whole folder and run CameraDesk.exe.
echo For source development, follow README.md to install dependencies.
pause
