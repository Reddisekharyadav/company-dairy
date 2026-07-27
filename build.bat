@echo off
setlocal enabledelayedexpansion

REM Build virtual environment if needed
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt

REM Create dist executable using the PyInstaller spec
py -3 -m PyInstaller --clean worksense.spec
if %ERRORLEVEL% neq 0 (
    echo PyInstaller build failed
    exit /b %ERRORLEVEL%
)

echo Build complete. Executable in dist\WorkSense\WorkSense.exe
echo Use installer\WorkSenseAI-Setup.iss with Inno Setup to create installer.
endlocal
