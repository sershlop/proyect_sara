Option Explicit

If WScript.Arguments.Count <> 1 Then WScript.Quit 2

Dim shell, entorno, fso, archivo, estadoPath, estado, contenido, patron, estilo
Set shell = CreateObject("WScript.Shell")
Set entorno = shell.Environment("PROCESS")
Set fso = CreateObject("Scripting.FileSystemObject")
archivo = WScript.Arguments(0)
estadoPath = fso.BuildPath(fso.BuildPath(fso.GetParentFolderName(archivo), "runtime"), "estado.json")
estilo = 1
If fso.FileExists(estadoPath) Then
    Set estado = fso.OpenTextFile(estadoPath, 1, False)
    contenido = estado.ReadAll
    estado.Close
    Set patron = New RegExp
    patron.Pattern = """webview2""\s*:\s*""presente"""
    If patron.Test(contenido) Then estilo = 0
End If
entorno("SARA_LAUNCH_HIDDEN") = "1"
shell.Run """" & archivo & """", estilo, False
