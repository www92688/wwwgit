; Inno Setup 安装脚本（详设 第八章；D4 范围：脚本交付，实机安装验证人工执行）
; 用法：Inno Setup Compiler 打开本文件 → Compile
; 产物：dist/installer/YuChongGou_Setup_<version>.exe

#define MyAppName "源重构"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "YuChongGou Contributors"
#define MyAppExeName "YuChongGou.exe"

[Setup]
AppId={{8E5F6A2C-4B7D-4E3A-9C1F-2A7B9D0E5C11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\YuChongGou
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=YuChongGou_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Win10+（需求 4.4）
MinVersion=10.0
; 可选签名（有证书时取消注释）
;SignTool=signtool

[Languages]
; 中文语言包自 Inno Setup 6.3 起不再随官方安装包分发（非官方翻译），
; 为保证本机/CI 构建可复现，随仓库分发：scripts/languages/ChineseSimplified.isl
; （来源 jrsoftware/issrc is-6_7_3 tag，BSD 风格许可，见 LICENSE-isl.txt）
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller COLLECT 产物目录整体打入
Source: "..\dist\YuChongGou\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载保留用户数据：%LOCALAPPDATA%\YuChongGou（配置/数据库/缓存）不删除——默认行为即保留，无需条目
