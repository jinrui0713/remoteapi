Set WshShell = CreateObject("WScript.Shell")
CurrentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Log file path
LogFile = CurrentDir & "\cloudflared.log"

' Command: Start cloudflared and output log to file (overwrite)
' --url: Quick Tunnel mode
' > LogFile 2>&1: Redirect stdout and stderr to log file
Command = "cmd /c cloudflared.exe tunnel --url http://localhost:8000 > """ & LogFile & """ 2>&1"

' 0 = Hide window, False = Do not wait for termination
WshShell.Run Command, 0, False
