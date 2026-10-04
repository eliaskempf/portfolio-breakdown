; Per-user installation. Portfolio workspaces are deliberately outside {app}.
[Setup]
AppId={{6D349059-F435-44AF-BBD7-FC10D66F665B}
AppName=Portfolio Breakdown
AppVersion={#AppVersion}
AppPublisher=Portfolio Breakdown contributors
DefaultDirName={localappdata}\Programs\Portfolio Breakdown
DefaultGroupName=Portfolio Breakdown
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=portfolio-breakdown-{#AppVersion}-windows-x64-setup
SetupIconFile={#BundleDir}\_internal\portfolio_app\assets\favicon.ico
UninstallDisplayIcon={app}\Portfolio Breakdown.exe
LicenseFile={#BundleDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "webview"; Description: "Install Microsoft Edge WebView2 for the app window (requires internet)"; Check: not HasWebView2

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Bootstrapper}"; Flags: dontcopy

[Icons]
Name: "{group}\Portfolio Breakdown"; Filename: "{app}\Portfolio Breakdown.exe"; WorkingDir: "{app}"
Name: "{group}\Portfolio Breakdown (browser)"; Filename: "{app}\Portfolio Breakdown.exe"; Parameters: "--browser"; WorkingDir: "{app}"
Name: "{userdesktop}\Portfolio Breakdown"; Filename: "{app}\Portfolio Breakdown.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Portfolio Breakdown.exe"; Description: "Open Portfolio Breakdown"; Flags: nowait postinstall skipifsilent; Check: HasWebView2
Filename: "{app}\Portfolio Breakdown.exe"; Parameters: "--browser"; Description: "Open Portfolio Breakdown in your browser"; Flags: nowait postinstall skipifsilent; Check: not HasWebView2

[Code]
function HasRuntimeAt(Root: Integer): Boolean;
var Version: String;
begin
  Result := RegQueryStringValue(Root,
    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version)
    and (Version <> '') and (Version <> '0.0.0.0');
end;

function HasWebView2: Boolean;
begin
  Result := HasRuntimeAt(HKCU32) or HasRuntimeAt(HKCU64)
    or HasRuntimeAt(HKLM32) or HasRuntimeAt(HKLM64);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if not HasWebView2 and WizardIsTaskSelected('webview') then begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
      '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) or not HasWebView2 then
      Result := 'WebView2 could not be installed. Check your internet connection and retry, '
        + 'or go Back and deselect WebView2 to use the browser shortcut.';
  end;
end;
