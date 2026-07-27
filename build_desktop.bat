@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  WorkSense - Build Single EXE for Desktop
echo ============================================
echo.

REM Activate venv if it exists
if exist "venv\Scripts\activate.bat" (
    echo [1/5] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo [1/5] No venv found - using system Python
)

REM Ensure required packages
echo [2/5] Checking PyInstaller...
pip install pyinstaller --quiet

REM Clean previous build artifacts to avoid stale files
echo [3/5] Cleaning old build artifacts...
if exist "build\worksense_onefile" (
    rmdir /S /Q "build\worksense_onefile"
)
if exist "dist\WorkSense.exe" (
    del /F /Q "dist\WorkSense.exe"
)

REM Build single-file exe
echo [4/5] Building WorkSense.exe (single file)...
echo       This may take 2-5 minutes, please wait...
python -m PyInstaller --noconfirm worksense_onefile.spec

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Build failed! Check output above for errors.
    pause
    exit /b %ERRORLEVEL%
)

REM Verify the exe was created
if not exist "dist\WorkSense.exe" (
    echo [ERROR] dist\WorkSense.exe not found after build!
    pause
    exit /b 1
)

REM Copy to Desktop
echo [5/5] Copying WorkSense.exe to Desktop...
set "DESKTOP=%USERPROFILE%\Desktop"
copy /Y "dist\WorkSense.exe" "%DESKTOP%\WorkSense.exe"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ============================================
    echo  SUCCESS!
    echo  WorkSense.exe is now on your Desktop.
    echo.
    echo  Double-click it to start tracking.
    echo  It runs silently in the background.
    echo  Open http://127.0.0.1:8000 in your browser
    echo  to see the live dashboard.
    echo ============================================
) else (
    echo [WARNING] Could not copy to Desktop.
    echo           Manually copy: dist\WorkSense.exe
    echo           Size: 
    for %%I in ("dist\WorkSense.exe") do echo           %%~zI bytes
)

echo.
pause
