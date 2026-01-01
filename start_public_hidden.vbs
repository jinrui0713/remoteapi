Set WshShell = CreateObject("WScript.Shell")
CurrentDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' ログファイルのパス
LogFile = CurrentDir & "\cloudflared.log"

' コマンド: cloudflaredを起動し、ログをファイルに出力（上書き）
' --url: Quick Tunnelモード
' > LogFile 2>&1: 標準出力とエラー出力をログファイルへ
Command = "cmd /c cloudflared.exe tunnel --url http://localhost:8000 > """ & LogFile & """ 2>&1"

' 0 = ウィンドウを隠す, False = 終了を待たない
WshShell.Run Command, 0, False
