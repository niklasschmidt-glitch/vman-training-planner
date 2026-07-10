@echo off
setlocal
cd /d "%~dp0"
title VMAN Training Planner 1.00 - Windows build

echo.
echo ===============================================
echo   VMAN Training Planner 1.00 - Windows build
echo ===============================================
echo.

where py >nul 2>nul
if errorlevel 1 goto NO_PYTHON

echo [1/3] Installerer noedvendige build-pakker...
py -3 -m ensurepip --upgrade >nul 2>nul
py -3 -m pip install --disable-pip-version-check -r requirements_windows.txt
if errorlevel 1 goto FAILED

echo.
echo [2/3] Bygger programmet...
py -3 -m PyInstaller --noconfirm --clean --windowed --noupx ^
  --name "VMAN Training Planner 1.00" ^
  --icon "vman_engine\assets\vman_training_planner.ico" ^
  --add-data "vman_engine\assets;vman_engine\assets" ^
  --add-data "vman_engine\manuals;vman_engine\manuals" ^
  --add-data "presets;presets" ^
  run_app.py
if errorlevel 1 goto FAILED

if not exist "dist\VMAN Training Planner 1.00\VMAN Training Planner 1.00.exe" goto FAILED

copy /Y "README_FOR_USERS.txt" "dist\VMAN Training Planner 1.00\README.txt" >nul

echo.
echo [3/3] Pakker den faerdige udgave som zip...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path 'dist\VMAN Training Planner 1.00' -DestinationPath 'VMAN_Training_Planner_1.00_Windows.zip' -Force"
if errorlevel 1 goto FAILED

echo.
echo ===============================================
echo   FAERDIG
echo ===============================================
echo.
echo Programmet ligger her:
echo dist\VMAN Training Planner 1.00\VMAN Training Planner 1.00.exe
echo.
echo Filen til Dropbox ligger her:
echo VMAN_Training_Planner_1.00_Windows.zip
echo.
start "" "dist\VMAN Training Planner 1.00"
pause
exit /b 0

:NO_PYTHON
echo.
echo Python blev ikke fundet.
echo Installer Python 3 fra python.org og marker "Add python.exe to PATH".
echo Koer derefter denne fil igen.
echo.
pause
exit /b 1

:FAILED
echo.
echo Buildet mislykkedes. Fejlen staar ovenfor.
echo.
pause
exit /b 1
