# Build instructions

Install dependencies:

```powershell
py -3 -m pip install -r requirements.txt
```

Run locally:

```powershell
py -3 main.py
```

Build executable with PyInstaller:

```powershell
py -3 -m PyInstaller --noconsole --clean worksense.spec
```

Create installer with Inno Setup (open `installer\WorkSenseAI-Setup.iss` in Inno Setup):

```powershell
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\WorkSenseAI-Setup.iss
```
