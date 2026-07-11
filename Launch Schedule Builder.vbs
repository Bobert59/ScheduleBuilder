Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
shell.CurrentDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
shell.Run "pyw -3.12 -m schedule_builder.gui", 0, False
