@echo off
echo ========================================================
echo   WorkSense AI — Android APK Builder
echo ========================================================

cd /d "%~dp0"

echo.
echo [1/3] Refreshing web assets in www/...
venv\Scripts\python.exe -c "import os, shutil; os.makedirs('www/static', exist_ok=True); shutil.copy('backend/templates/index.html', 'www/index.html'); shutil.copytree('backend/static', 'www/static', dirs_exist_ok=True); print('Web assets synced!')"

echo.
echo [2/3] Syncing Capacitor Android project...
npx cap sync android

echo.
echo [3/3] Building Android APK package...
cd android
call gradlew.bat assembleDebug

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Gradle APK build failed. Ensure JDK 17+ / Android SDK is installed.
    exit /b %ERRORLEVEL%
)

echo.
echo ========================================================
echo   SUCCESS! Android APK built successfully!
echo   APK Location: android\app\build\outputs\apk\debug\app-debug.apk
echo ========================================================
pause
