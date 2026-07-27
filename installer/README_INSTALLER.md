# WorkSense AI Installer

This folder contains an Inno Setup script to create a Windows installer for WorkSense AI.

## Steps

1. Build the application with PyInstaller:

```powershell
cd d:\project\companydairy
python -m pip install -r requirements.txt
python -m PyInstaller --noconsole --onefile --clean worksense.spec
```

2. Install Inno Setup on Windows and run:

```powershell
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "d:\project\companydairy\installer\WorkSenseAI-Setup.iss"
```

3. The generated installer will appear in the current working directory or as configured by Inno Setup.
