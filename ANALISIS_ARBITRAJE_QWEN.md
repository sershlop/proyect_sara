# 🔍 Análisis: Por qué Qwen se repite y no usa lo que ya aprendió

## 📊 Flujo Actual (PROBLEMA)

```
Usuario: "abre notion"
    ↓
brain.procesar() 
    ├─ Busca en BD: comando 'abrir_notion' → score 0.88
    ├─ Busca en file_watcher: archivo 'Notion.lnk' → score 0.90
    ├─ Candidatos = [Notion.lnk (0.90), abrir_notion (0.88)]
    ├─ Margen = 0.90 - 0.88 = 0.02 (< 0.8 MARGEN_EMPATE)
    ├─ ❌ LLAMA A QWEN ← AQUÍ EMPIEZA EL PROBLEMA
    │
    └─ Qwen elige: "abrir_notion es comando, Notion.lnk es archivo"
         ↓
sara.procesar_resultado(resultado_brain, comando)
    ├─ Ejecuta: abrir_notion
    ├─ Éxito ✓
    ├─ Pregunta: "¿Era esto lo que pediste?"
    ├─ Usuario: "sí" ✓
    └─ Guarda en DB:
       ├─ palabras_clave de comando: "abre notion, abre notion"
       └─ ❌ NO GUARDA EN cache_intenciones ← AQUÍ ESTÁ EL BUG

─────────────────────────────────────────────

Usuario (5 segundos después): "abre notion"
    ↓
brain.procesar()
    ├─ ❌ NO CONSULTA cache_intenciones
    ├─ Busca nuevamente en BD y file_watcher
    ├─ Candidatos VUELVEN A EMPATAR (porque no cambiaron los scores)
    ├─ VUELVE A LLAMAR A QWEN 🔄 ← PROBLEMA: SIN APRENDER DEL ANTERIOR
    │
    └─ Qwen vuelve a decir lo mismo...
         ↓
🔁 El ciclo se repite infinitamente
```

## 🐛 Raíz del Problema: 3 Fallas

### 1️⃣ **NO EXISTE CONSULTA A CACHÉ EN BRAIN.PY**
- Archivo: `brain.py` línea 1 (función `procesar()`)
- Problema: El sistema calcula candidatos sin verificar primero si ya existe una decisión guardada
- Evidencia: `Select-String` en brain.py NO encuentra referencias a `cache_intenciones`

```python
# Esto NO existe en brain.py:
def procesar(texto_original):
    texto_limpio = normalizar_texto(texto_original)
    
    # ❌ FALTA ESTO:
    resultado_cached = obtener_cache_intencion(texto_limpio)
    if resultado_cached:
        logger.debug("brain", f"Usando caché: {resultado_cached}")
        return resultado_cached  # ← Evita todo el trabajo nuevamente
    
    # Resto del código...
```

### 2️⃣ **NO GUARDA EN CACHÉ CUANDO EL USUARIO CONFIRMA**
- Archivo: `sara.py` línea 410
- Problema: Al guardar palabras clave, NO se popula `cache_intenciones`
- Código actual:

```python
if confirmar:
    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
    id_cmd = comando.get("id")
    if id_cmd:
        from database import agregar_palabras_clave_comando
        agregar_palabras_clave_comando(id_cmd, palabras_clave)
        logger.info("sara", f"Palabras clave agregadas a cmd id={id_cmd}: '{palabras_clave}'")
        # ❌ FALTA GUARDAR EN CACHÉ:
        # from database import guardar_cache_intencion
        # guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=3600)
```

### 3️⃣ **LAS PALABRAS CLAVE NO EVITAN EL EMPATE**
- Archivo: `brain.py` línea 350-360 (función `buscar_comando()`)
- Problema: Aunque se agreguen palabras clave, el otro candidato (archivo) sigue compitiendo
- El scoring sigue siendo similar porque:
  - "Notion.lnk" es un archivo que coincide bien con "abre notion"
  - El comando "abrir_notion" con palabras clave "abre notion" también coincide
  - El margen sigue siendo pequeño → EMPATE NUEVAMENTE

## 📁 Tablas Involucradas

### `cache_intenciones` (línea 141 en database.py)
```sql
CREATE TABLE IF NOT EXISTS cache_intenciones (
    texto_limpio   TEXT PRIMARY KEY,      -- "abre notion"
    resultado      TEXT,                  -- JSON con el resultado completo
    ts             DATETIME,              -- Timestamp de cuando se guardó
    ttl_segundos   INTEGER DEFAULT 30     -- Tiempo de expiración (30 seg)
)
```

✅ **Funciones disponibles:**
- `guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=30)`
- `obtener_cache_intencion(texto_limpio)`
- `limpiar_cache_vencido()`

❌ **Se usan en:** database.py únicamente
❌ **NO se usan en:** brain.py (donde debería consultarse)

## 🎯 La Solución en 3 Pasos

### PASO 1: Consultar caché al inicio de brain.procesar()
**Archivo:** `brain.py` línea 580 (en `procesar()`)
```python
def procesar(texto_original):
    # ──────────────────────────────────────────
    # NUEVO: Consultar caché PRIMERO
    # ──────────────────────────────────────────
    resultado_cached = obtener_cache_intencion(texto_limpio)
    if resultado_cached:
        logger.debug("brain", f"✓ Caché encontrada: {resultado_cached.get('tipo')}")
        return resultado_cached
    
    # ──────────────────────────────────────────
    # Resto del procesamiento normal...
    # ──────────────────────────────────────────
```

### PASO 2: Guardar en caché cuando el usuario confirma
**Archivo:** `sara.py` línea 410 (después de `agregar_palabras_clave_comando()`)
```python
if confirmar:
    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
    id_cmd = comando.get("id")
    if id_cmd:
        from database import agregar_palabras_clave_comando, guardar_cache_intencion
        agregar_palabras_clave_comando(id_cmd, palabras_clave)
        
        # NUEVO: Guardar en caché para 1 hora
        guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=3600)
        logger.info("sara", f"✓ Caché guardada y palabras clave agregadas a cmd id={id_cmd}")
```

### PASO 3: Guardar en caché también en confirmación de archivos
**Archivo:** `sara.py` línea 450 (cuando confirma archivo)
```python
if io_manager.preguntar_si_no(""):
    cmd = resultado.get("comando")
    if cmd:
        from database import incrementar_acceso_archivo, guardar_cache_intencion
        incrementar_acceso_archivo(cmd.get("accion", ""))
        
        # NUEVO: Guardar en caché
        guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=3600)
```

## 📈 Flujo Mejorado (CON CACHÉ)

```
Usuario: "abre notion"
    ↓
brain.procesar()
    ├─ ✓ CONSULTA cache_intenciones con "abre notion"
    ├─ Cache VACÍA (primera vez)
    ├─ Procede con búsqueda normal...
    ├─ Llama a Qwen (por el empate)
    └─ Retorna resultado
         ↓
sara.procesar_resultado()
    ├─ Ejecuta comando
    ├─ Usuario confirma "sí"
    └─ ✓ Guarda en cache_intenciones(texto_limpio="abre notion", resultado={...}, ttl=3600)

─────────────────────────────────────────────

Usuario (5 segundos después): "abre notion"
    ↓
brain.procesar()
    ├─ ✓ CONSULTA cache_intenciones con "abre notion"
    ├─ ✓ ENCONTRADO EN CACHÉ (vigente)
    ├─ ✓ RETORNA RESULTADO INMEDIATAMENTE
    ├─ Sin búsqueda, sin Qwen, sin espera
    └─ Muy rápido ⚡

🎯 Resultado: SARA RECUERDA LA DECISIÓN ARBITRADA
```

## 🔑 Puntos Clave

| Aspecto | Actual | Mejorado |
|---------|--------|----------|
| **Consulta caché** | ❌ No | ✅ Sí, al inicio |
| **Guarda caché** | ❌ No | ✅ Sí, al confirmar |
| **Repeticiones Qwen** | 🔄 Infinitas | ⏱️ 1 vez cada hora |
| **Velocidad 2da búsqueda** | ⏳ Lenta (Qwen) | ⚡ Instantánea |
| **TTL (expiración)** | N/A | 3600 seg (1 hora) |

## 🚀 Próximos Pasos

1. ✅ Agregar consulta de caché en `brain.procesar()`
2. ✅ Agregar guardado de caché en `sara.procesar_resultado()`
3. ✅ Importar funciones necesarias en ambos archivos
4. ✅ Probar: "abre notion" → Qwen → "sí" → "abre notion" nuevamente (sin Qwen)
