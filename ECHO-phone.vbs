' ECHO (phone mode): same as ECHO.vbs but listens on your Wi-Fi so a paired iPhone can open it.
' Only private-network devices that opened the "Open on phone" link (with its private key) are let in.
Option Explicit
Dim sh, http, fso, wmi, p, app, pyw, server, url, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set http = CreateObject("MSXML2.XMLHTTP")
app = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = app & "\.venv\Scripts\pythonw.exe"
server = app & "\app\server.py"
url = "http://127.0.0.1:8765/"

Function InfoBind()
  On Error Resume Next
  InfoBind = ""
  http.Open "GET", url & "api/info", False
  http.Send
  If Err.Number = 0 And http.Status = 200 Then InfoBind = http.responseText
  Err.Clear
End Function

If InStr(InfoBind(), """bind"":""lan""") = 0 Then
  ' stop a local-only ECHO server, then restart it in phone mode
  Set wmi = GetObject("winmgmts:\\.\root\cimv2")
  For Each p In wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name LIKE 'python%'")
    If Not IsNull(p.CommandLine) Then
      If InStr(1, p.CommandLine, "Programs\ECHO\app\server.py", 1) > 0 Then p.Terminate
    End If
  Next
  WScript.Sleep 800
  sh.Environment("Process")("ECHO_BIND") = "lan"
  sh.Environment("Process")("ECHO_NO_BROWSER") = "1"
  sh.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & server & Chr(34), 0, False
  For i = 1 To 90
    WScript.Sleep 400
    If InStr(InfoBind(), """bind"":""lan""") > 0 Then Exit For
  Next
End If
sh.Run url, 1, False
