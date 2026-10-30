# auditar_tablas.py — ¿Qué tablas de sara.db se USAN de verdad? (solo lectura, no modifica nada)
# Uso (en la carpeta de SARA, con SARA cerrada o abierta):   python auditar_tablas.py
# Para cada tabla: filas, última escritura (si hay columna de tiempo), y en qué .py se LEE / ESCRIBE.
# Veredicto: VIVA (se escribe y se lee) · SOLO-ESCRIBE (se llena y nadie la consulta) · MUERTA (vacía o sin código).
import os, re, sqlite3, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "sara.db"
COLS_TIEMPO = ("ultimo_ts", "timestamp", "ts", "fecha", "creado", "creado_en", "ultima_vez", "ultimo_uso",
               "fecha_creacion", "fecha_hora", "dia", "actualizado")

def fuentes():
    for p in BASE.glob("*.py"):
        if p.name.startswith("test_") or p.name == Path(__file__).name:
            continue
        try:
            yield p.name, p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

def main():
    if not DB.exists():
        print("No encuentro sara.db junto a este script."); return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tablas = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    codigo = list(fuentes())
    print(f"sara.db: {len(tablas)} tablas · {len(codigo)} módulos .py revisados\n")
    resumen = []
    for t in tablas:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        ultimo = "-"
        col_t = next((c for c in cols if c.lower() in COLS_TIEMPO), None)
        if col_t and n:
            try:
                v = con.execute(f'SELECT MAX("{col_t}") FROM "{t}"').fetchone()[0]
                if isinstance(v, (int, float)) and v > 1e9:
                    v = time.strftime("%Y-%m-%d %H:%M", time.localtime(v))
                ultimo = f"{col_t}={v}"
            except Exception:
                pass
        lee, escribe = set(), set()
        pat_t = re.compile(rf'\b{re.escape(t)}\b')
        for nombre, txt in codigo:
            for m in pat_t.finditer(txt):
                ctx = txt[max(0, m.start() - 90): m.end() + 90].upper()
                if re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", ctx):
                    escribe.add(nombre)
                if re.search(r"\b(SELECT|FROM|JOIN)\b", ctx):
                    lee.add(nombre)
        if n == 0 and not lee and not escribe:
            v = "MUERTA (vacía y sin código que la toque)"
        elif n == 0:
            v = "VACÍA (hay código, pero nunca se ha llenado: ¿ruta que no se ejecuta?)"
        elif escribe and not lee:
            v = "SOLO-ESCRIBE (se llena y nadie la consulta)"
        elif lee:
            v = "VIVA"
        else:
            v = "REVISAR"
        resumen.append((t, n, v))
        print(f"■ {t}  [{n} filas]  último: {ultimo}")
        print(f"   columnas: {', '.join(cols)[:140]}")
        print(f"   lee: {', '.join(sorted(lee)) or '—'}")
        print(f"   escribe: {', '.join(sorted(escribe)) or '—'}")
        print(f"   → {v}\n")
    print("══ RESUMEN ══")
    for t, n, v in resumen:
        print(f"{v.split(' ')[0]:<13} {t} ({n})")
    con.close()

if __name__ == "__main__":
    main()
