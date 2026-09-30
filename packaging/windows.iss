#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef Accelerator
  #define Accelerator "nvidia"
#endif

[Setup]
AppId={{8A753184-6AE2-47A0-96ED-F9C67E965A18}
AppName=OpenDance
AppVersion={#AppVersion}
AppPublisher=OpenDance contributors
DefaultDirName={localappdata}\Programs\OpenDance
DefaultGroupName=OpenDance
OutputDir=..\release
OutputBaseFilename=OpenDance-{#AppVersion}-windows-x86_64-{#Accelerator}-setup
PrivilegesRequired=lowest
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1900000000
SlicesPerDisk=1
WizardStyle=modern
SetupIconFile=..\build\opendance.ico
UninstallDisplayIcon={app}\OpenDance.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\OpenDance\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OpenDance"; Filename: "{app}\OpenDance.exe"
Name: "{autodesktop}\OpenDance"; Filename: "{app}\OpenDance.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OpenDance.exe"; Description: "Launch OpenDance"; Flags: nowait postinstall skipifsilent
