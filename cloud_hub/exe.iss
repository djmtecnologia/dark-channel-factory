[Setup]
AppName=Dark Channel Worker
AppVersion=1.0
AppPublisher=DJM Tecnologia
DefaultDirName={autopf}\DarkChannelWorker
DefaultGroupName=Dark Channel Worker
OutputBaseFilename=Instalador_DarkWorker
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
; Aponte para onde o PyInstaller jogou o seu EXE
Source: "C:\Users\User\.gemini\antigravity\scratch\dark-channel-factory\dist\desktop_node.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; O atalho normal que a pessoa clica se quiser configurar uma conta nova
Name: "{group}\Conectar Conta Google"; Filename: "{app}\desktop_node.exe"; Parameters: "--setup"

; Atalho padrão de desinstalação
Name: "{group}\Desinstalar"; Filename: "{uninstallexe}"

[Registry]
; Esta chave mágica faz o EXE iniciar automaticamente escondido quando o Windows ligar
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DarkChannelWorker"; ValueData: """{app}\desktop_node.exe"""; Flags: uninsdeletevalue

[Run]
; Inicia o Worker imediatamente após a instalação sem mostrar janela
Filename: "{app}\desktop_node.exe"; Flags: nowait postinstall runhidden; Description: "Iniciar o motor do Worker agora"