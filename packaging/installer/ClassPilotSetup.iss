; Single-user installer source. Compile only after Inno Setup is approved and installed.
#define ProductName "ClassPilot"
#define ProductVersion "0.1.0"
#define ProductExe "ClassPilot.exe"

[Setup]
AppId={{21D898F2-9E9F-4EC8-B109-AC4E9701D100}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppPublisher=Yau2004
DefaultDirName={localappdata}\Programs\ClassPilot
DisableDirPage=no
DefaultGroupName=ClassPilot
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64os
ArchitecturesAllowed=x64os
UsePreviousAppDir=no
Uninstallable=yes
UninstallDisplayName=ClassPilot
InfoBeforeFile=..\..\docs\RELEASE_PATH_LIMITATION.txt
OutputDir=..\..\dist\installer
OutputBaseFilename=ClassPilotSetup
Compression=lzma2/ultra64
SolidCompression=yes
ExtraDiskSpaceRequired=94_371_840
WizardStyle=modern
CloseApplications=no
RestartApplications=no
; The installer deliberately does not request elevation, install system runtimes,
; create services, or include the user-selected PostgreSQL ZIP.

[Languages]
Name: "chinesetraditional"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"

[Messages]
SelectDirDesc=選擇 ClassPilot 程式檔的安裝位置。此選項只變更程式位置；設定、憑證、PostgreSQL 與使用者資料仍儲存在 %LOCALAPPDATA%\ClassPilot。
FinishedLabel=ClassPilot 程式檔已安裝。請關閉此視窗後，從桌面或開始功能表手動開啟。首次資料設定必須在一般使用者權限下進行。
UninstalledAll=ClassPilot 已成功解除安裝。使用者資料會保留；建議重開機後再安裝新版。
UninstalledMost=ClassPilot 解除安裝完成，但有些程式檔未能移除。請先關閉仍在執行的 ClassPilot，並建議重開機後再安裝新版。使用者資料會保留。
UninstalledAndNeedsRestart=要完成 ClassPilot 的解除安裝，Windows 需要重新啟動。您想要現在重新啟動嗎？

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他捷徑："; Flags: unchecked

[Files]
Source: "..\..\dist\windows-user-zip\ClassPilot\ClassPilot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows-user-zip\ClassPilot\*"; DestDir: "{app}"; Excludes: "ClassPilot.exe,Data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\Data"; Flags: uninsneveruninstall

[Icons]
Name: "{autoprograms}\ClassPilot"; Filename: "{app}\{#ProductExe}"
Name: "{autodesktop}\ClassPilot"; Filename: "{app}\{#ProductExe}"; Tasks: desktopicon

[Code]
const
  ProductUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{21D898F2-9E9F-4EC8-B109-AC4E9701D100}_is1';

function HasCjkPathCharacter(const Value: String): Boolean;
var
  I, CodePoint, LowSurrogate: Integer;
begin
  Result := False;
  for I := 1 to Length(Value) do
  begin
    CodePoint := Ord(Value[I]);
    if ((CodePoint >= $D840) and (CodePoint <= $D87F)) and (I < Length(Value)) then
    begin
      LowSurrogate := Ord(Value[I + 1]);
      if (LowSurrogate >= $DC00) and (LowSurrogate <= $DFFF) then
      begin
        CodePoint := $10000 + ((CodePoint - $D800) * $400) + (LowSurrogate - $DC00);
        if ((CodePoint >= $20000) and (CodePoint <= $3134F)) then
        begin
          Result := True;
          Exit;
        end;
      end;
    end;
    if ((CodePoint >= $3400) and (CodePoint <= $4DBF)) or
       ((CodePoint >= $4E00) and (CodePoint <= $9FFF)) or
       ((CodePoint >= $F900) and (CodePoint <= $FAFF)) or
       ((CodePoint >= $3040) and (CodePoint <= $30FF)) or
       ((CodePoint >= $AC00) and (CodePoint <= $D7AF)) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function PathProblemMessage(const LabelText, PathText: String): String;
begin
  Result := LabelText + '：' + PathText + #13#10;
end;

function InitializeSetup(): Boolean;
var
  Problem: String;
  ExistingUninstall: String;
  DefaultDataPath: String;
begin
  Problem := '';
  ExistingUninstall := '';
  if RegQueryStringValue(HKCU, ProductUninstallKey, 'UninstallString', ExistingUninstall) then
  begin
    MsgBox('已找到此 Windows 使用者的 ClassPilot 安裝。更新檢查、資料相容性、備份與資料庫遷移確認流程尚未完成，因此本安裝程式不會覆寫既有程式。' + #13#10#13#10 +
      '請取消安裝並保留目前程式與資料。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  DefaultDataPath := ExpandConstant('{localappdata}\ClassPilot');
  if HasCjkPathCharacter(GetTempDir()) then
    Problem := Problem + PathProblemMessage('Windows 暫存位置', GetTempDir());
  Result := Problem = '';
  if not Result then
    MsgBox('此版本的 PostgreSQL 初始化不支援含中文、日文或韓文的使用者資料或暫存路徑。安裝已停止，尚未建立資料庫。' + #13#10#13#10 +
      Problem + #13#10 + '請取消安裝並等待支援版本；不要改 Windows 帳戶、改用共用系統位置或放寬權限。程式安裝位置若是唯一問題，可在安裝頁改選不含上述文字的每使用者路徑。空格可使用。',
      mbError, MB_OK);
  if Result and HasCjkPathCharacter(DefaultDataPath) then
    MsgBox('預設資料位置含中文、日文或韓文，目前 PostgreSQL 初始化不支援該路徑。程式仍可安裝；首次設定時請選擇不含上述文字的本機固定磁碟資料目錄，例如 D:\ClassPilotData。若程式安裝、暫存或實際資料路徑仍含上述文字，首次設定會在建立資料庫前停止。空格可使用。',
      mbInformation, MB_OK);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
  TargetDir, DriveRoot, ProbeBase, ParentPath, ProbeDir: String;
  FreeMB, TotalMB: Cardinal;
  SearchRec: TFindRec;
  HasExistingContent: Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Problem := '';
    TargetDir := RemoveBackslashUnlessRoot(WizardDirValue());
    if HasCjkPathCharacter(WizardDirValue()) then
      Problem := Problem + PathProblemMessage('ClassPilot 程式安裝位置', WizardDirValue());
    if HasCjkPathCharacter(ExpandConstant('{localappdata}\ClassPilot\runtime\postgresql\bin\initdb.exe')) then
      Problem := Problem + PathProblemMessage('PostgreSQL executable 位置', ExpandConstant('{localappdata}\ClassPilot\runtime\postgresql\bin\initdb.exe'));
    if HasCjkPathCharacter(ExpandConstant('{localappdata}\ClassPilot\data\postgresql')) then
      Problem := Problem + PathProblemMessage('PostgreSQL 資料位置', ExpandConstant('{localappdata}\ClassPilot\data\postgresql'));
    if HasCjkPathCharacter(GetTempDir()) then
      Problem := Problem + PathProblemMessage('Windows 暫存位置', GetTempDir());

    DriveRoot := ExtractFileDrive(TargetDir) + '\';
    if (ExtractFileDrive(TargetDir) = '') or (not DirExists(DriveRoot)) then
      Problem := Problem + PathProblemMessage('安裝磁碟不存在或不是本機磁碟', TargetDir)
    else if not GetSpaceOnDisk(DriveRoot, True, FreeMB, TotalMB) then
      Problem := Problem + PathProblemMessage('無法檢查安裝磁碟可用空間', DriveRoot)
    else if FreeMB < 150 then
      Problem := Problem + '安裝磁碟可用空間不足：目前約 ' + IntToStr(FreeMB) + ' MB，至少需要 150 MB。' + #13#10;

    HasExistingContent := False;
    if (Problem = '') and DirExists(TargetDir) and FindFirst(TargetDir + '\*', SearchRec) then
    begin
      try
        repeat
          if (SearchRec.Name <> '.') and (SearchRec.Name <> '..') then
          begin
            HasExistingContent := True;
            Break;
          end;
        until not FindNext(SearchRec);
      finally
        FindClose(SearchRec);
      end;
    end;
    if HasExistingContent then
      Problem := Problem + PathProblemMessage('目標資料夾已有檔案，為避免覆寫而停止', TargetDir);

    ProbeBase := TargetDir;
    while (Problem = '') and (not DirExists(ProbeBase)) and (ProbeBase <> DriveRoot) do
    begin
      if FileExists(ProbeBase) then
      begin
        Problem := Problem + PathProblemMessage('安裝路徑中有檔案阻擋資料夾建立', ProbeBase);
        Break;
      end;
      ParentPath := ExtractFileDir(ProbeBase);
      if (ParentPath = '') or (ParentPath = ProbeBase) then
        Break;
      ProbeBase := ParentPath;
    end;
    if (Problem = '') then
    begin
      if not DirExists(ProbeBase) then
        Problem := Problem + PathProblemMessage('找不到可檢查寫入權限的既存資料夾', ProbeBase);
    end;
    if (Problem = '') then
    begin
      ProbeDir := GenerateUniqueName(ProbeBase, '.classpilot-write-check');
      if not CreateDir(ProbeDir) then
        Problem := Problem + PathProblemMessage('無法在此安裝位置的既存資料夾建立權限測試，請檢查寫入權限', ProbeBase)
      else if not RemoveDir(ProbeDir) then
        Problem := Problem + PathProblemMessage('已建立暫時權限檢查資料夾，但無法移除', ProbeDir);
    end;

    Result := Problem = '';
    if not Result then
      MsgBox('所選安裝位置或必要路徑未通過檢查。安裝尚未開始，也未建立資料庫。' + #13#10#13#10 +
        Problem + #13#10 + '若問題是程式安裝磁碟、容量、寫入權限或程式路徑，請在此頁調整路徑後重試。若問題是使用者資料、PostgreSQL executable 或 Windows 暫存路徑，請取消安裝並等待支援版本。此版本不支援含中文、日文或韓文的相關路徑；空格可使用。資料仍存放於 %LOCALAPPDATA%\ClassPilot。不要改 Windows 帳戶或放寬權限。', mbError, MB_OK);
  end;
end;
