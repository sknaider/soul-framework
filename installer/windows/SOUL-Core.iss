#ifndef MyAppVersion
  #define MyAppVersion "0.4.3"
#endif

[Setup]
AppId={{8E53D246-7B5A-4E25-A2BB-6D0E3DB1A443}
AppName=SOUL Core
AppVersion={#MyAppVersion}
AppVerName=SOUL Core {#MyAppVersion}
AppPublisher=SOUL
AppPublisherURL=https://soulsmemory.com
DefaultDirName={localappdata}\Programs\SOUL Core
DefaultGroupName=SOUL Core
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\windows
OutputBaseFilename=SOUL-Core-{#MyAppVersion}-Windows-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
UninstallDisplayName=SOUL Core {#MyAppVersion}
UninstallDisplayIcon={app}\python.exe
LicenseFile=..\..\LICENSE
InfoAfterFile=README-INSTALL.txt

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\..\build\windows-installer\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Configurar mi alma"; Filename: "{app}\soul-setup.cmd"; WorkingDir: "{app}"
Name: "{group}\Terminal de SOUL Core"; Filename: "{app}\soul-terminal.cmd"; WorkingDir: "{app}"
Name: "{group}\Diagnóstico de SOUL Core"; Filename: "{app}\soul-doctor.cmd"; WorkingDir: "{app}"
Name: "{group}\Desinstalar SOUL Core"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SOUL Core"; Filename: "{app}\soul-setup.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\soul-setup.cmd"; Description: "Crear y conectar mi primera alma"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := IsWin64;
  if not Result then
    MsgBox('SOUL Core requiere Windows de 64 bits.', mbError, MB_OK);
end;

function InitializeUninstall(): Boolean;
begin
  if not UninstallSilent then
    MsgBox('El programa se quitará, pero tus almas y memorias en %USERPROFILE%\.soul se conservarán.', mbInformation, MB_OK);
  Result := True;
end;
