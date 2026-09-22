' Silent desktop launcher for Voxoryl — no console window.
' Recreate the Desktop icon (only Voxoryl.lnk — never "Voxoryl (start)"):
'   .\.venv\Scripts\python.exe .\scripts\launch_voxoryl.py --shortcut
'   powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop-shortcut.ps1
' Optional: pass --dashboard to also open the web console in the browser.
' Uses --native (pywebview) by default; pass --browser-widget to force Edge --app.
Option Explicit
Dim sh, fso, root, pyw, script, args, i, argLine, hasNative, hasBrowser
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pyw = root & "\.venv\Scripts\pythonw.exe"
script = root & "\scripts\launch_voxoryl.py"
If Not fso.FileExists(pyw) Then
  pyw = root & "\.venv\Scripts\python.exe"
End If
argLine = ""
hasNative = False
hasBrowser = False
For i = 0 To WScript.Arguments.Count - 1
  argLine = argLine & " " & WScript.Arguments(i)
  If LCase(WScript.Arguments(i)) = "--native" Then hasNative = True
  If LCase(WScript.Arguments(i)) = "--browser-widget" Then hasBrowser = True
Next
If (Not hasNative) And (Not hasBrowser) Then
  argLine = argLine & " --native"
End If
sh.CurrentDirectory = root
' 0 = hidden; False = don't wait (supervisor lives until widget closes)
sh.Run """" & pyw & """ """ & script & """" & argLine, 0, False
