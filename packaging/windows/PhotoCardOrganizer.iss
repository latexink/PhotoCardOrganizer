#ifndef SourceDir
  #error SourceDir must point to the PyInstaller bundle.
#endif
#ifndef AppVersion
  #error AppVersion must be supplied by the build script.
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by the build script.
#endif

#define AppName "Photo Card Organizer"
#define AppExe "PhotoCardOrganizer.exe"
#define AppMutexName "PhotoCardOrganizer.App.1"
#define ProductRegistryKey "Software\PhotoCardOrganizer"

[Setup]
AppId={{88A215E6-7853-4B4B-BFD4-FCE119514E0D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Photo Card Organizer
DefaultDirName={localappdata}\Programs\PhotoCardOrganizer
DefaultGroupName=Photo Card Organizer
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
DisableFinishedPage=no
AllowNoIcons=no
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PhotoCardOrganizer-Installer-{#AppVersion}
SetupIconFile={#SourceDir}\PhotoCardOrganizer.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
CloseApplications=yes
RestartApplications=no
AppMutex={#AppMutexName}
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallLogMode=append
SignedUninstaller=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=Photo Card Organizer Installer and Uninstaller

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenu"; Description: "Create a Start Menu shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "autostart"; Description: "Start monitoring when I sign in"; GroupDescription: "Background monitoring:"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{userdesktop}\Photo Card Organizer.lnk"
Type: files; Name: "{userprograms}\Photo Card Organizer\Photo Card Organizer.lnk"
Type: files; Name: "{userstartup}\Photo Card Organizer.lnk"
Type: files; Name: "{userstartup}\PhotoCardOrganizer.cmd"
Type: files; Name: "{app}\PhotoCardOrganizer-*-User-Guide.pdf"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Photo Card Organizer\Photo Card Organizer"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{userprograms}\Photo Card Organizer\User Guide"; Filename: "{app}\PhotoCardOrganizer-{#AppVersion}-User-Guide.pdf"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{userprograms}\Photo Card Organizer\Changelog"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\CHANGELOG.md"""; WorkingDir: "{app}"; Tasks: startmenu
Name: "{userprograms}\Photo Card Organizer\Uninstall Photo Card Organizer"; Filename: "{uninstallexe}"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{userdesktop}\Photo Card Organizer"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\Photo Card Organizer"; Filename: "{app}\{#AppExe}"; Parameters: "--service"; WorkingDir: "{app}"; Tasks: autostart

[Registry]
Root: HKCU; Subkey: "{#ProductRegistryKey}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{#ProductRegistryKey}"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{#ProductRegistryKey}"; ValueType: string; ValueName: "UninstallPath"; ValueData: "{uninstallexe}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch Photo Card Organizer"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
var
  InstalledVersion: String;
  MaintenancePage: TInputOptionWizardPage;
  MaintenanceExit: Boolean;
  RemoveUserData: Boolean;
  UserDataRemovalFailed: Boolean;

procedure InitializeWizard;
var
  ModeName: String;
begin
  MaintenancePage := nil;
  MaintenanceExit := False;
  if RegQueryStringValue(HKCU, '{#ProductRegistryKey}', 'Version', InstalledVersion) then
  begin
    if InstalledVersion = '{#AppVersion}' then
      ModeName := 'Repair'
    else
      ModeName := 'Upgrade';
    WizardForm.WelcomeLabel1.Caption := ModeName + ' {#AppName}';
    WizardForm.WelcomeLabel2.Caption :=
      'Setup found version ' + InstalledVersion + '. This ' + Lowercase(ModeName) +
      ' keeps your settings, card profiles, transfer records, and library manifests.';
    MaintenancePage := CreateInputOptionPage(
      wpWelcome,
      'Choose a maintenance action',
      'What would you like Setup to do?',
      'Repair or upgrade preserves application data. Uninstall asks separately whether ' +
      'per-user settings and local logs should also be removed.',
      True,
      False);
    MaintenancePage.Add(ModeName + ' {#AppName} (recommended)');
    MaintenancePage.Add('Uninstall {#AppName}');
    MaintenancePage.SelectedValueIndex := 0;
  end
  else
  begin
    WizardForm.WelcomeLabel2.Caption :=
      'This is the {#AppName} installer and uninstaller. It installs for this Windows ' +
      'account without administrator access. PhotoCardOrganizer.exe is the application ' +
      'launcher; application data is stored separately so future upgrades preserve it.';
  end;
end;

function InstalledUninstallCommand(var CommandLine: String): Boolean;
var
  UninstallPath: String;
begin
  Result := False;
  CommandLine := '';
  if RegQueryStringValue(
    HKCU, '{#ProductRegistryKey}', 'UninstallPath', UninstallPath) and
    FileExists(UninstallPath) then
  begin
    CommandLine := AddQuotes(UninstallPath);
    Result := True;
    exit;
  end;

  if RegQueryStringValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
      '{88A215E6-7853-4B4B-BFD4-FCE119514E0D}_is1',
    'UninstallString',
    CommandLine) then
    Result := Trim(CommandLine) <> '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  CommandLine: String;
  ResultCode: Integer;
begin
  Result := True;
  if (MaintenancePage = nil) or
    (CurPageID <> MaintenancePage.ID) or
    (MaintenancePage.SelectedValueIndex <> 1) then
    exit;

  Result := False;
  if MsgBox(
    'Uninstall {#AppName} from this computer?' + #13#10 + #13#10 +
    'The uninstaller will ask whether to preserve per-user settings and local logs. ' +
    'Imported media and library records are never removed.',
    mbConfirmation,
    MB_YESNO) <> IDYES then
    exit;

  if not InstalledUninstallCommand(CommandLine) then
  begin
    MsgBox(
      'The installed uninstaller could not be located. Run Repair first, then reopen ' +
      'this Installer executable and choose Uninstall.',
      mbError,
      MB_OK);
    exit;
  end;

  if not Exec('>', CommandLine, '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('Windows could not start the installed uninstaller.', mbError, MB_OK);
    exit;
  end;
  if ResultCode <> 0 then
  begin
    MsgBox(
      'The uninstaller did not complete successfully. Exit code: ' +
      IntToStr(ResultCode),
      mbError,
      MB_OK);
    exit;
  end;
  if RegKeyExists(HKCU, '{#ProductRegistryKey}') then
  begin
    MsgBox(
      'Uninstall was cancelled or did not complete. No setup changes were made.',
      mbInformation,
      MB_OK);
    exit;
  end;

  MaintenanceExit := True;
  WizardForm.Close;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if MaintenanceExit then
  begin
    Cancel := True;
    Confirm := False;
  end;
end;

function InitializeUninstall: Boolean;
var
  Answer: Integer;
begin
  RemoveUserData := False;
  UserDataRemovalFailed := False;
  if UninstallSilent then
  begin
    Result := True;
    exit;
  end;

  Answer := MsgBox(
    'Should the uninstaller also remove per-user settings, card profiles, and local application logs?' +
    '' + #13#10 + #13#10 +
    'Yes: remove this application data.' + #13#10 +
    'No: preserve it for a repair or future upgrade (recommended).' + #13#10 +
    'Cancel: stop without uninstalling.' + #13#10 + #13#10 +
    'Imported media, destination transfer records, and card metadata are never removed.',
    mbConfirmation, MB_YESNOCANCEL);
  if Answer = IDCANCEL then
  begin
    Result := False;
    exit;
  end;
  RemoveUserData := Answer = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDataPath: String;
begin
  if (CurUninstallStep = usPostUninstall) and RemoveUserData then
  begin
    UserDataPath := ExpandConstant('{userappdata}\PhotoCardOrganizer');
    if not DelTree(UserDataPath, True, True, True) then
    begin
      UserDataRemovalFailed := True;
      if not UninstallSilent then
        MsgBox('Some user data could not be removed from:' + #13#10 + UserDataPath,
          mbError, MB_OK);
    end;
  end;

  if (CurUninstallStep = usDone) and not UninstallSilent then
  begin
    if UserDataRemovalFailed then
      MsgBox(
        '{#AppName} was uninstalled, but some requested user data remains.' + #13#10 +
        'Imported media, destination transfer records, and card metadata were preserved.',
        mbInformation, MB_OK)
    else if RemoveUserData then
      MsgBox(
        '{#AppName} was uninstalled and its per-user application data was removed.' + #13#10 +
        'Imported media, destination transfer records, and card metadata were preserved.',
        mbInformation, MB_OK)
    else
      MsgBox(
        '{#AppName} was uninstalled. Settings, card profiles, and local application logs were preserved.' +
        '' + #13#10 + 'Imported media, destination transfer records, and card metadata were preserved.',
        mbInformation, MB_OK);
  end;
end;
