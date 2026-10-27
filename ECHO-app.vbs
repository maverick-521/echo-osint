' ECHO desktop app: opens ECHO in its own window (no browser tab). Server stays hidden.
Option Explicit
Dim sh, fso, app, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
app = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = app & "\.venv\Scripts\pythonw.exe"
sh.CurrentDirectory = app
sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & app & "\app\desktop.py" & Chr(34), 0, False
