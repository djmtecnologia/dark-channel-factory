[Setup]
AppName=Dark Channel Worker
AppVersion=1.0
AppPublisher=DJM Tecnologia
; Alterado para userappdata por causa do PrivilegesRequired=lowest
DefaultDirName={userappdata}\DarkChannelWorker 
DefaultGroupName=Dark Channel Worker
OutputBaseFilename=Instalador_DarkWorker
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
; 1. Caminho relativo: Procura a pasta dist diretamente dentro do projeto
Source: "dist\desktop_node.exe"; DestDir: "{app}"; Flags: ignoreversion

; 2. O SEGREDO: Copia o seu ficheiro .env para a pasta de instalação!
Source: ".env"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Conectar Conta Google"; Filename: "{app}\desktop_node.exe"; Parameters: "--setup"
Name: "{group}\Desinstalar"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DarkChannelWorker"; ValueData: """{app}\desktop_node.exe"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\desktop_node.exe"; Flags: nowait postinstall runhidden; Description: "Iniciar o motor do Worker agora"