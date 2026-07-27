[Setup]
AppName=WorkSense AI
AppVersion=1.0
DefaultDirName={pf}\WorkSense AI
DefaultGroupName=WorkSense AI
DisableProgramGroupPage=yes
OutputBaseFilename=WorkSenseAI-Setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\WorkSense\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WorkSense AI"; Filename: "{app}\WorkSense.exe"
Name: "{commondesktop}\WorkSense AI"; Filename: "{app}\WorkSense.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\WorkSense.exe"; Description: "Launch WorkSense AI"; Flags: nowait postinstall skipifsilent
