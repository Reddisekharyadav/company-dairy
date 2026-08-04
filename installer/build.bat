@echo off
echo ========================================================
echo   WorkSense AI — Build Desktop App & Installer
echo ========================================================

cd /d "%~dp0\.."

echo.
echo [1/3] Cleaning previous build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo.
echo [2/3] Building PyInstaller executable package...
venv\Scripts\pyinstaller.exe worksense_onefile.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: PyInstaller build failed!
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] PyInstaller build successful!
echo Package built in: dist\WorkSense\
echo.
echo Optional: To compile the setup installer executable (.exe), install Inno Setup and run:
echo "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\worksense.iss
echo ========================================================
