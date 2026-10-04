Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run "pyw -3.11 """ & folder & "\web_app.py"" --no-browser", 0, False
