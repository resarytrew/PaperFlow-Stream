#define MyAppName "Чистовик"
#define MyAppVersion "0.3.2"
#define MyAppPublisher "Чистовик"
#define MyAppExeName "Chistovik.exe"

[Setup]
AppId={{A5D4D704-1F95-4F0C-9C42-4D7F26495F53}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=0.3.2.0
DefaultDirName={localappdata}\Programs\Чистовик
DefaultGroupName=Чистовик
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\release
OutputBaseFilename=ChistovikSetup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=force
RestartApplications=no

[Files]
Source: "..\..\dist\Chistovik\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Чистовик"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"
Name: "{userdesktop}\Чистовик"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык «Чистовик» на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--set-web-url ""{param:WebUrl|http://127.0.0.1:17841}"""; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install-autostart"; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Parameters: "--background"; Description: "Запустить «Чистовик»"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall-autostart"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
  MsgBox(
    'Учебные данные и резервные копии в папке пользователя удалены не будут.',
    mbInformation,
    MB_OK
  );
end;
