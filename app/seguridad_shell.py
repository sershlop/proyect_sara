# 📁 seguridad_shell.py — Análisis de riesgo de comandos PowerShell para SARA
#
# Se usa desde shell.py:  evaluar_riesgo(comando, perfil) -> Veredicto
#
# Diseño (defensa en capas, "fail-closed"):
#   1. NEGRA ABSOLUTA  : nunca, en ningún perfil (discos, arranque, antivirus, credenciales, ofuscación...).
#   2. RUTAS CRÍTICAS  : si el comando contiene un verbo destructivo Y toca una ruta crítica -> NEGRA,
#                        sin importar el ORDEN (arregla `gci C:\Windows | ri -r`) ni el estilo de la ruta
#                        (C:/Windows, C:\Win*, C:\Windows\..\Windows, \\?\C:\Windows, %windir%, 8.3...).
#   3. DESTINO NO VERIFICABLE: verbo destructivo + variables/concatenaciones/.NET -> no se puede saber a qué
#                        apunta -> NEGRA (perfil normal) o AMARILLA REFORZADA (desarrollador).
#   4. NEGRA RELAJABLE : cosas sensibles pero legítimas para desarrolladores -> NEGRA en perfil normal,
#                        AMARILLA REFORZADA (confirmación escrita) en perfil desarrollador.
#   5. AMARILLA REFORZADA: acciones grandes (borrado recursivo, apagar el equipo...).
#   6. BLANCA          : solo lectura, sintaxis estricta y anclada.
#   7. AMARILLA        : todo lo demás -> pide permiso.
#
# La BD de shell.py tiene CHECK(zona IN ('blanca','amarilla','negra')): por eso "reforzada" es un FLAG
# del veredicto y no una cuarta zona.

import fnmatch
import os
import re
from dataclasses import dataclass

PERFIL_NORMAL = "normal"
PERFIL_DEV = "desarrollador"


@dataclass(frozen=True)
class Veredicto:
    zona: str                 # 'blanca' | 'amarilla' | 'negra'
    reforzada: bool = False   # True -> confirmación escrita (no basta un "sí")
    motivo: str = ""


def _neg(motivo):  return Veredicto("negra", False, motivo)
def _ref(motivo):  return Veredicto("amarilla", True, motivo)


# ═══════════════════════════════════════════════════════════════════════════
# 1. PREPARACIÓN DEL TEXTO
# ═══════════════════════════════════════════════════════════════════════════

_SYS_DRIVE = os.environ.get("SystemDrive", "C:").lower()
_WINDIR = os.environ.get("SystemRoot", _SYS_DRIVE + "\\Windows").lower()
_ENV_CONOCIDAS = {
    "systemroot": _WINDIR, "windir": _WINDIR,
    "programfiles": os.environ.get("ProgramFiles", _SYS_DRIVE + "\\Program Files").lower(),
    "programfiles(x86)": os.environ.get("ProgramFiles(x86)", _SYS_DRIVE + "\\Program Files (x86)").lower(),
    "programdata": os.environ.get("ProgramData", _SYS_DRIVE + "\\ProgramData").lower(),
    "systemdrive": _SYS_DRIVE, "homedrive": _SYS_DRIVE,
    "userprofile": os.environ.get("USERPROFILE", _SYS_DRIVE + "\\Users\\usuario").lower(),
    "homepath": "\\users\\usuario",
}
_RE_ENV = re.compile(r"%([\w()]+)%|\$env:(\w+)|\$\{env:([^}]+)\}")


def _expandir_env(m):
    nombre = (m.group(1) or m.group(2) or m.group(3)).lower()
    if nombre in _ENV_CONOCIDAS:
        return _ENV_CONOCIDAS[nombre]
    valor = os.environ.get(nombre.upper())
    return valor.lower() if valor else m.group(0)


def _forma_ruta(comando: str) -> str:
    """Versión del comando pensada para analizar RUTAS (no para detectar switches)."""
    s = comando.lower()
    s = _RE_ENV.sub(_expandir_env, s)
    s = s.replace("/", "\\").replace("'", " ").replace('"', " ")
    s = re.sub(r"\\\\[?.]\\", "", s)                      # \\?\C:\ y \\.\C:\ -> C:\
    s = re.sub(r"(?<=[^\s\\])\\{2,}", r"\\", s)           # C:\\Windows -> C:\Windows
    s = re.sub(r"\b([a-z]):(?=[^\\\s])", r"\1:\\", s)     # c:windows -> c:\windows
    s = re.sub(r"\\\.(?=\\|\s|$)", "", s)                 # \.\ -> \
    previo = None
    while previo != s:                                    # resolver \x\..\
        previo = s
        s = re.sub(r"\\[^\\\s]+\\\.\.(?=\\|\s|$)", "", s)
    return s


# ═══════════════════════════════════════════════════════════════════════════
# 2. RUTAS CRÍTICAS
# ═══════════════════════════════════════════════════════════════════════════

_RE_CRIT_DIR = re.compile(
    r"(?<![\w.])[a-z]:\\(?:windows|program files(?: \(x86\))?|programdata|recovery|boot|efi|"
    r"system volume information|\$recycle\.bin)(?=[\\\s*?;,)|&]|$)"
)
_RE_CRIT_FRAGMENTOS = re.compile(
    r"(?<![\w-])(?:system32|syswow64|winsxs)(?![\w-])|\\drivers\\etc(?=\\|\s|$)|\\start menu\\programs\\startup\b"
    r"|\\\\[^\\\s]+\\[a-z]\$"                              # \\host\c$ (recurso administrativo)
)
_RE_CRIT_ENV = re.compile(r"%(?:systemroot|windir|programfiles|programdata)%|\$\{?env:(?:systemroot|windir|programfiles|programdata)")
_RE_RAIZ_O_PERFIL = re.compile(
    r"^(?:[a-z]:\\?\*{0,2}"                                # C:  C:\  C:\*
    r"|[a-z]:\\users(?:\\[^\\]*)?\\?\*?"                   # C:\Users  C:\Users\*  C:\Users\bob
    r"|~\\?\*?|\$home\\?\*?|\$env:userprofile\\?\*?)$"
)
_CRITICAS_ABS = (_SYS_DRIVE + "\\windows", _SYS_DRIVE + "\\windows\\system32", _SYS_DRIVE + "\\program files",
                 _SYS_DRIVE + "\\program files (x86)", _SYS_DRIVE + "\\programdata", _SYS_DRIVE + "\\users")


def _corto_8_3(token: str) -> str:
    """Expande nombres cortos (PROGRA~1) en Windows; en otros sistemas devuelve igual."""
    if "~" not in token or os.name != "nt":
        return token
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetLongPathNameW(token, buf, 1024):
            return buf.value.lower()
    except Exception:
        pass
    return token


def toca_ruta_critica(comando: str) -> bool:
    r = _forma_ruta(comando)
    if _RE_CRIT_DIR.search(r) or _RE_CRIT_FRAGMENTOS.search(r) or _RE_CRIT_ENV.search(comando.lower()):
        return True
    for t in re.split(r"[\s,;|&()<>=]+", r):
        t = _corto_8_3(t.strip())
        if not t:
            continue
        if _RE_RAIZ_O_PERFIL.match(t):
            return True
        if any(c in t for c in "*?[") and re.match(r"^[a-z]:\\", t):
            prefijo = re.split(r"[*?\[]", t, 1)[0]
            for crit in _CRITICAS_ABS:
                if fnmatch.fnmatch(crit, t.rstrip("\\")) or crit.startswith(prefijo):
                    return True
        if re.search(r"~\d", t) and re.match(r"^[a-z]:\\", t):     # 8.3 que no se pudo expandir
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 3. VERBOS DESTRUCTIVOS Y DESTINO DINÁMICO
# ═══════════════════════════════════════════════════════════════════════════

_RE_DESTRUCTIVO = re.compile(
    r"(?<![\w-])(?:remove-item|ri|rm|rmdir|rd|del|erase|move-item|mi|move|mv|rename-item|rni|ren|"
    r"clear-content|clc|set-content|sc|add-content|ac|out-file|tee-object|tee|attrib|cipher|takeown|icacls|"
    r"new-item|ni|remove-itemproperty|rp|set-itemproperty|sp|clear-itemproperty|clp)(?![\w-])"
    r"|>|\[(?:system\.)?io\.(?:file|directory|path)\]|\.delete\s*\(|\.moveto|\.copyto|\.movefile"
    r"|(?<![\w-])robocopy\b.*\s/(?:mir|purge|mov)"
)
_RE_BORRADO = re.compile(r"(?<![\w-])(?:remove-item|ri|rm|rmdir|rd|del|erase)(?![\w-])")
_RE_RECURSIVO = re.compile(r"(?<![\w-])(?:-rec\w*|-r|-fo\w*|-force|/s|/q)(?![\w-])|[*?]|\|")

_RE_DINAMICO = re.compile(
    r"\$(?!env:|\{env:|_(?![\w])|psitem\b|null\b|true\b|false\b)[\w{(]"   # variables (salvo $env, $_ y constantes)
    r"|['\"]\s*\+\s*['\"]|-join\b|-replace\b|-f\s+['\"]|\[char\]|::|\.\.(?=\\|/)|\bget-variable\b|\bgv\b"
)


def _es_destino_dinamico(comando: str) -> bool:
    return bool(_RE_DINAMICO.search(comando.lower()))


# ═══════════════════════════════════════════════════════════════════════════
# 4. LISTAS DE PATRONES (sobre el comando en minúsculas)
# ═══════════════════════════════════════════════════════════════════════════

_NEGRA_ABSOLUTA = (
    (r"\bformat-volume\b|\bclear-disk\b|\bremove-partition\b|\binitialize-disk\b|\bset-disk\b|\bdiskpart\b|\bdiskshadow\b", "tocar discos/particiones"),
    (r"(?<![\w-])format(?:\.com)?\s+[a-z]:", "formatear un disco"),
    (r"\bcipher(?:\.exe)?\s+/w|\bsdelete\b|\bfsutil\b.*(?:setzerodata|deletejournal)|\bconvert\s+[a-z]:\s+/fs", "borrado seguro/conversión de disco"),
    (r"[\\/]{2}\.[\\/](?:physicaldrive|[a-z]:)|\bdd\s+(?:if|of)=", "acceso crudo a disco"),
    (r"\bbcdedit\b|\bbootrec\b|\breagentc\b|\bpnputil\b.*(?:delete|remove)|\bdism\b.*(?:/remove|/disable|/cleanup-image)", "arranque/drivers/imagen del sistema"),
    (r"\bvssadmin\b.*\bdelete\b|\bwbadmin\b.*\bdelete\b|win32_shadowcopy|\bwmic\b.*shadowcopy", "destruir respaldos"),
    (r"\bwevtutil\b.*\b(?:cl|clear-log)\b|\b(?:clear|remove)-eventlog\b", "borrar bitácoras del sistema"),
    (r"\b(?:set|add|remove)-mppreference\b|\buninstall-windowsfeature\b|\bdisable-windowsoptionalfeature\b", "modificar antivirus/características"),
    (r"\bset-netfirewallprofile\b.*-enabled\s+(?:false|0|\$false)|\bnetsh\b.*firewall.*\b(?:off|disable)\b", "apagar firewall"),
    (r"\bsc(?:\.exe)?\s+(?:stop|delete|config)\s+(?:windefend|wscsvc|mpssvc|eventlog|winmgmt|sense|wdnissvc)\b"
     r"|\b(?:stop|set|remove)-service\b.*\b(?:windefend|mpssvc|eventlog|wscsvc|winmgmt|sense)\b", "servicios de seguridad"),
    (r"\bmimikatz\b|\bsekurlsa\b|\blsass\b|\bntds\.dit\b|\\config\\(?:sam|security|system)\b|\bprocdump\b|comsvcs.*minidump"
     r"|\breg(?:\.exe)?\s+save\b.*\b(?:sam|security|system)\b|\bvaultcmd\b|\bcmdkey\b|key\s*=\s*clear", "robo de credenciales"),
    (r"\b(?:stop-process|taskkill|kill|spps)\b.*\b(?:winlogon|csrss|lsass|wininit|smss|services|svchost|dwm|registry)\b"
     r"|\btaskkill\b.*/im\s+\*|\bget-process\b\s*\*?\s*\|\s*(?:stop-process|kill|spps)\b|\bstop-process\b.*-name\s+\*", "matar procesos vitales/masivo"),
    (r"-encodedcommand\b|(?<![\w-])-enc?\s+[a-z0-9+/=]{8,}|\bfrombase64string\b|\[convert\]::frombase64|\[char\]\s*\d", "comando codificado/ofuscado"),
    (r"\bdownloadstring\b|\bdownloaddata\b", "ejecución sin archivo (fileless)"),
    (r"&\s*[\$(]|\.invoke\s*\(|\[scriptblock\]::create|\binvoke-command\b|\benter-pssession\b|\bnew-pssession\b|\bpsexec\b|\bwinrs\b"
     r"|\benable-psremoting\b|\binvoke-(?:wmi|cim)method\b|\bremove-(?:wmiobject|ciminstance)\b|\bwmic\b.*(?:process\s+call|\bdelete\b)", "ejecución dinámica/remota"),
    (r"\bmshta\b|\bbitsadmin\b|\brundll32\b|\bcertutil\b.*-(?:urlcache|decode|encode)", "binarios usados para malware (LOLBins)"),
    (r"\bnet1?\s+user\b.*(?:/add|/delete)|\bnet1?\s+localgroup\s+administrators\b|\b(?:new|remove|set|disable)-localuser\b|\badd-localgroupmember\b", "cuentas/administradores"),
)

# (patrón, motivo) -> en perfil desarrollador pasan a AMARILLA REFORZADA
_NEGRA_RELAJABLE = (
    (r"\bschtasks\b.*/(?:create|change)\b|\bregister-scheduledtask\b|\bnew-service\b|\bsc(?:\.exe)?\s+create\b|currentversion\\run", "persistencia (tareas/servicios/arranque)"),
    (r"\bregsvr32\b|\b(?:wscript|cscript)\b|\binstallutil\b", "registrar/ejecutar scripts de host"),
    (r"-verb\s+runas\b", "pide elevación a administrador"),
    (r"\bnet\.webclient\b|\b(?:invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer)\b.*\.(?:exe|msi|ps1|bat|cmd|vbs|js|dll|scr|jar|hta)\b", "descarga de ejecutables/scripts"),
    (r"\badd-type\b.*-(?:typedefinition|memberdefinition)\b|\[(?:system\.)?reflection\.assembly\]", "código .NET en caliente"),
    (r"\b(?:takeown|icacls)\b", "cambiar permisos/propietario"),
    (r"\w`\w", "acento grave dentro de una palabra (posible ofuscación)"),
)

_REFORZADA = (
    (r"(?<![\w-])(?:shutdown|stop-computer|restart-computer|logoff|hibernate)(?![\w-])", "apaga/reinicia/cierra sesión"),
    (r"\bdisable-netadapter\b|\bnetsh\b.*\binterface\b.*\bdisable\b|\bset-netipaddress\b", "puede dejarte sin red"),
    (r"\bclear-recyclebin\b|\bformat-table\b.*\bremove\b", "vacía la papelera"),
)

_RE_IEX = re.compile(r"(?<![\w-])(?:iex|invoke-expression)(?![\w-])")
_RE_IEX_DESCARGA_LITERAL = re.compile(
    r"\s*\(?\s*(?:irm|iwr|invoke-restmethod|invoke-webrequest|curl|wget)\s+(?:-uri\s+)?https://[\w\-./?=&%:~#+@]+"
    r"(?:\s+-usebasicparsing)?\s*\)?\s*\|\s*(?:iex|invoke-expression)\s*"
)
_RE_EXEC_POLICY = re.compile(r"\bset-executionpolicy\b")
_RE_REGISTRO = re.compile(r"\breg(?:\.exe)?\s+(?:delete|add|import|load|restore|copy|unload)\b"
                          r"|\b(?:remove|set|new|clear|rename|copy|move)-item(?:property)?\b.*(?:\bhk[a-z_]*:|registry::)")
_RE_HIVE_USUARIO = re.compile(r"\bhkcu\b|hkey_current_user")
_RE_HIVE_SISTEMA = re.compile(r"\bhklm\b|hkey_local_machine|\bhkcr\b|\bhku\b|hkey_users|hkey_classes_root|registry::")


# ═══════════════════════════════════════════════════════════════════════════
# 5. ZONA BLANCA (solo lectura, anclada)
# ═══════════════════════════════════════════════════════════════════════════

_ARGS = r"(?:\s+[\w\-\.:\\/,*~'\" ]*)?"
_NO_UNC = r"(?!.*(?:^|[\s'\"])\\\\)"
_BLANCA_BASE = (
    r"dir", r"ls", r"gci", r"get-childitem", r"get-process", r"gps", r"get-date", r"get-location", r"pwd",
    r"get-service", r"get-volume", r"get-netipaddress", r"hostname", r"whoami", r"ipconfig", r"systeminfo",
)
_BLANCA_DEV = (   # lectura típica de desarrolladores; sin '=' ni '|', así no entran --output=, -o file, etc.
    r"git\s+(?:status|log|diff|branch|remote\s+-v|show|stash\s+list|--version)",
    r"(?:python|python3|py|node|npm|npx|pip|pip3|git|docker|dotnet|java|go|cargo|rustc)\s+(?:--version|-v|-version|version)",
    r"(?:pip|pip3)\s+(?:list|freeze)", r"npm\s+(?:list|ls|outdated)", r"docker\s+(?:ps|images|version)",
    r"get-command", r"where\.exe", r"tasklist", r"netstat\s+-ano", r"get-nettcpconnection", r"get-psdrive",
)


def _es_blanca(crudo: str, perfil: str) -> bool:
    if any(op in crudo for op in (";", "|", "&", "`", "$", ">", "<", "(", ")", "{", "}", "\n", "\r", "@", "%")):
        return False
    lista = _BLANCA_BASE + (_BLANCA_DEV if perfil == PERFIL_DEV else ())
    return any(re.search(r"(?is)^" + _NO_UNC + c + _ARGS + r"$", crudo.strip()) for c in lista)


# ═══════════════════════════════════════════════════════════════════════════
# 6. EVALUACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def evaluar_riesgo(comando: str, perfil: str = PERFIL_NORMAL, patrones_extra_negros=()) -> Veredicto:
    """
    patrones_extra_negros: pásale config.PATRONES_ZONA_NEGRA para conservar TODO lo que ya tenías
    (solo se aplica en perfil normal; en desarrollador mandan las listas de este módulo).
    """
    dev = perfil == PERFIL_DEV
    crudo = re.sub(r"\s+", " ", (comando or "").strip().lower())
    if not crudo:
        return Veredicto("amarilla", False, "comando vacío")

    # 1) negra absoluta
    for patron, motivo in _NEGRA_ABSOLUTA:
        if re.search(patron, crudo):
            return _neg(motivo)

    # 2) destructivo + ruta crítica (en cualquier orden)
    destructivo = bool(_RE_DESTRUCTIVO.search(crudo))
    if destructivo and toca_ruta_critica(comando):
        return _neg("toca una ruta crítica del sistema (Windows, Program Files, raíz de disco o perfil)")

    # 3) destructivo con destino no verificable
    if destructivo and _es_destino_dinamico(comando):
        if dev:
            return _ref("el destino del borrado/escritura se calcula con variables: revisa el comando completo")
        return _neg("no puedo verificar a dónde apunta (variables/concatenaciones/.NET)")

    # 4) iex: solo pasa la descarga literal https | iex y solo en dev
    if _RE_IEX.search(crudo):
        if dev and _RE_IEX_DESCARGA_LITERAL.fullmatch(crudo):
            return _ref("descarga y ejecuta un script remoto: verifica la URL")
        return _neg("Invoke-Expression sobre contenido no verificable")

    # 5) Set-ExecutionPolicy
    if _RE_EXEC_POLICY.search(crudo):
        if dev and re.search(r"-scope\s+(?:process|currentuser)\b", crudo):
            return _ref("cambia la política de ejecución solo para tu sesión/usuario")
        return _neg("cambia la política de ejecución del equipo")

    # 6) registro
    if _RE_REGISTRO.search(crudo):
        if dev and _RE_HIVE_USUARIO.search(crudo) and not _RE_HIVE_SISTEMA.search(crudo):
            return _ref("modifica el Registro del usuario actual (HKCU)")
        return _neg("modifica el Registro del sistema")

    # 7) negra relajable (+ lista previa de config en perfil normal)
    for patron, motivo in _NEGRA_RELAJABLE:
        if re.search(patron, crudo):
            return _ref(motivo) if dev else _neg(motivo)
    if not dev:
        for patron in patrones_extra_negros:
            if re.search(patron, crudo):
                return _neg("coincide con una regla de zona negra de config.py")

    # 8) reforzadas
    for patron, motivo in _REFORZADA:
        if re.search(patron, crudo):
            return _ref(motivo)
    if _RE_BORRADO.search(crudo) and _RE_RECURSIVO.search(crudo):
        return _ref("borrado masivo/recursivo/forzado")

    # 9) blanca / amarilla
    if _es_blanca(crudo, perfil):
        return Veredicto("blanca", False, "solo lectura")
    return Veredicto("amarilla", False, "requiere confirmación")


# ═══════════════════════════════════════════════════════════════════════════
# 7. CAPA DE EJECUCIÓN (la usa shell.py)
# ═══════════════════════════════════════════════════════════════════════════

# Modo restringido de PowerShell: bloquea Add-Type, llamadas .NET arbitrarias, COM, etc.
# Solo se antepone a comandos de zona BLANCA (no rompe nada porque son cmdlets simples).
PRELUDIO_CLM = "$ExecutionContext.SessionState.LanguageMode='ConstrainedLanguage'; "
