# 🚀 Plan de Implementación: Solucionar el Ciclo de Qwen

## 🎯 Objetivo Final
Cuando Qwen arbitre una decisión y el usuario confirme, SARA **recuerda esa decisión** en futuras búsquedas similares durante 1 hora, evitando llamadas innecesarias a Qwen.

---

## 📋 Etapas de Implementación

### **ETAPA 1: Agregar consulta de caché en `brain.py`** ⏱️ 5 min

**Archivo:** `brain.py`  
**Línea:** ~585 (dentro de función `procesar()`)

**Tarea:**
```python
# Después de: texto_limpio = normalizar_texto(texto_original)
# Y después de verificar si texto_limpio es vacío
# Agregar esto:

from database import obtener_cache_intencion

resultado_cached = obtener_cache_intencion(texto_limpio)
if resultado_cached:
    logger.debug("brain", f"✓ Caché encontrada para '{texto_limpio}'")
    return resultado_cached
```

**Ubicación exacta en brain.py:**
```python
def procesar(texto_original):
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino

    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")
    
    # 👇 INSERTAR AQUÍ 👇
    from database import obtener_cache_intencion
    resultado_cached = obtener_cache_intencion(texto_limpio)
    if resultado_cached:
        logger.debug("brain", f"✓ Caché encontrada para '{texto_limpio}'")
        return resultado_cached
    # 👆 HASTA AQUÍ 👆
    
    candidatos = []
    # ... resto del código ...
```

**Verificar:** Que el import sea local (dentro de la función) para evitar circular imports.

---

### **ETAPA 2: Guardar caché cuando confirma decisión arbitrada (COMANDOS)** ⏱️ 5 min

**Archivo:** `sara.py`  
**Línea:** ~410-430

**Tarea:**
Cambiar el import de:
```python
from database import agregar_palabras_clave_comando
```

A:
```python
from database import agregar_palabras_clave_comando, guardar_cache_intencion
```

Y agregar después de `agregar_palabras_clave_comando(id_cmd, palabras_clave)`:
```python
# 🆕 Guardar en caché la decisión arbitrada
guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
logger.info("sara", f"✓ Caché guardada: '{texto_mostrar}' → {resultado.get('tipo')}")
```

**Ubicación exacta en sara.py:**
```python
if confirmar:
    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
    
    id_cmd = comando.get("id")
    if id_cmd:
        from database import agregar_palabras_clave_comando, guardar_cache_intencion  # ← Modificar
        agregar_palabras_clave_comando(id_cmd, palabras_clave)
        logger.info("sara", f"Palabras clave agregadas a cmd id={id_cmd}: '{palabras_clave}'")
        
        # 🆕 Guardar en caché
        guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
        logger.info("sara", f"✓ Caché guardada: '{texto_mostrar}' → {resultado.get('tipo')}")
    else:
        # ... resto del código ...
```

---

### **ETAPA 3: Guardar caché en aprendizaje nuevo por arbitraje** ⏱️ 3 min

**Archivo:** `sara.py`  
**Línea:** ~425-435

**Tarea:**
Dentro de `else:` block, después de `aprender_comando(...)`, agregar:

```python
if resultado_ap.get("exito"):
    logger.info("sara", f"Arbitraje guardado: '{entrada_original[:50]}'")
    # 🆕 Guardar en caché
    guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
    logger.info("sara", f"✓ Caché guardada (nuevo comando): '{texto_mostrar}'")
elif resultado_ap.get("accion") == "duplicada":
    logger.info("sara", "Comando ya existe, entrada registrada como variación")
    # 🆕 Guardar en caché también para duplicada
    guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
```

---

### **ETAPA 4: Guardar caché en confirmación de archivos** ⏱️ 3 min

**Archivo:** `sara.py`  
**Línea:** ~455-465

**Tarea:**
Cambiar:
```python
from database import incrementar_acceso_archivo
```

A:
```python
from database import incrementar_acceso_archivo, guardar_cache_intencion
```

Y después de `agregar_palabras_clave_comando(id_cmd, palabras_clave_nuevas)`, agregar:
```python
# 🆕 Guardar en caché la decisión confirmada
guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
logger.info("sara", f"✓ Caché guardada: archivo '{nombre_archivo}'")
```

---

## ⏱️ Tiempo Total Estimado
- **Etapa 1:** 5 minutos
- **Etapa 2:** 5 minutos
- **Etapa 3:** 3 minutos
- **Etapa 4:** 3 minutos
- **Pruebas:** 10 minutos

**TOTAL:** ~26 minutos

---

## ✅ Checklist de Implementación

### Antes de Empezar
- [ ] Tengo copia de seguridad de `brain.py` y `sara.py`
- [ ] Verifiqué que `obtener_cache_intencion()` existe en `database.py`
- [ ] Verifiqué que `guardar_cache_intencion()` existe en `database.py`

### Cambios en `brain.py`
- [ ] Agregué import local de `obtener_cache_intencion`
- [ ] Agregué la consulta de caché después de normalizar texto
- [ ] Agregué el logger debug para cuando se usa caché
- [ ] Probé `python -m py_compile brain.py` (sin errores)

### Cambios en `sara.py` (Sección 1: Comandos)
- [ ] Agregué `guardar_cache_intencion` al import
- [ ] Agregué la línea que guarda caché después de `agregar_palabras_clave_comando()`
- [ ] Agregué logger info
- [ ] Probé `python -m py_compile sara.py` (sin errores)

### Cambios en `sara.py` (Sección 2: Aprendizaje)
- [ ] Agregué `guardar_cache_intencion` dentro de `if resultado_ap.get("exito"):`
- [ ] Agregué `guardar_cache_intencion` dentro de `elif resultado_ap.get("accion") == "duplicada":`
- [ ] Probé compilación sin errores

### Cambios en `sara.py` (Sección 3: Archivos)
- [ ] Agregué `guardar_cache_intencion` al import
- [ ] Agregué la línea que guarda caché después de `agregar_palabras_clave_comando()`
- [ ] Probé compilación sin errores

### Pruebas Funcionales
- [ ] **Prueba 1:** "abre notion" → Qwen elige → "sí" → Debe decir "✓ Caché guardada"
- [ ] **Prueba 2:** "abre notion" nuevamente (5 seg) → Sin Qwen, usa caché
- [ ] **Prueba 3:** "abre notion" nuevamente (30 min+) → Caché expiró, pero palabras clave siguen ahí

---

## 🧪 Script de Prueba

Después de implementar, ejecuta en terminal:

```python
# Prueba 1: Ver que Qwen se llama y caché se guarda
# Interactuar: "abre notion"
# Esperar que pregunte "¿Era esto lo que pediste?"
# Responder: "si"
# Verificar que aparezca: "✓ Caché guardada"

# Prueba 2: Verificar que caché se usa
# Interactuar: "abre notion" (nuevamente)
# Verificar que aparezca: "✓ Caché encontrada para 'abre notion'"
# Verificar que NO aparezca: "Empate (..) → árbitro Qwen"

# Prueba 3: Verificar base de datos
python -c "
import sqlite3
conn = sqlite3.connect('sara.db')
cursor = conn.cursor()
cursor.execute('SELECT texto_limpio, resultado, ts FROM cache_intenciones LIMIT 5')
for row in cursor.fetchall():
    print(row[0], '→', row[1][:50] if row[1] else None)
conn.close()
"
```

---

## 📊 Antes vs Después (Resumen Visual)

### ❌ ANTES (Problema)
```
Tú: "abre notion"
    ↓ (sin caché, busca desde cero)
SARA: 🔍 Empate detectado
SARA: ℹ️ Árbitro Qwen
⏳ [Esperando Qwen]
SARA: ¿Era esto lo que pediste?
Tú: "si"
SARA: ✓ Guardado

─────────────────

Tú: "abre notion" (5 seg después)
    ↓ (sin caché, vuelve a empezar)
SARA: 🔍 Empate detectado
SARA: ℹ️ Árbitro Qwen  ← 🔄 REPETICIÓN
⏳ [Esperando Qwen nuevamente]
```

### ✅ DESPUÉS (Solucionado)
```
Tú: "abre notion"
    ↓ (sin caché, busca desde cero)
SARA: 🔍 Empate detectado
SARA: ℹ️ Árbitro Qwen
⏳ [Esperando Qwen]
SARA: ¿Era esto lo que pediste?
Tú: "si"
SARA: ✓ Caché guardada  ← 🆕 Ahora se guarda

─────────────────

Tú: "abre notion" (5 seg después)
    ↓ (CONSULTA CACHÉ PRIMERO)
SARA: 🔍 ✓ Caché encontrada  ← 🎯 ¡Usa lo anterior!
SARA: ℹ️ App abierta: abrir_notion
    ↓ (SIN QWEN, sin espera)
    ✅ Instantáneo
```

---

## 🎯 Notas Importantes

1. **Variable `texto_mostrar`**: Verifica que exista en el contexto de sara.py. Si no existe, usa `texto_limpio` en su lugar.

2. **TTL=3600**: La caché expira en 1 hora (3600 segundos). Puedes ajustar según necesites:
   - 300 = 5 minutos
   - 1800 = 30 minutos
   - 3600 = 1 hora (recomendado)
   - 86400 = 1 día

3. **Limpieza automática**: La tabla se limpia automáticamente cuando `obtener_cache_intencion()` detecta expiración.

---

## 🔗 Referencias en el Código

### Archivos que verás mencionados
- `database.py`: Contiene `guardar_cache_intencion()` y `obtener_cache_intencion()`
- `brain.py`: Donde se procesa la entrada del usuario (línea 580 aproximadamente)
- `sara.py`: Donde se confirman decisiones arbitradas (líneas 410, 430, 460)
- `logger.py`: Para registrar eventos (importado automáticamente)

### Funciones clave
```python
# En database.py
obtener_cache_intencion(texto_limpio)  # Retorna resultado o None
guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=30)  # Guarda en BD
limpiar_cache_vencido()  # Limpia expiradas (se llama automáticamente)
```

---

## ❓ Preguntas Frecuentes

**P: ¿Qué pasa si cambio el TTL a 0?**  
R: La caché no se guardará (TTL 0 = no cachear). No recomendado.

**P: ¿Puedo cachear decisiones que NO fueron arbitradas?**  
R: Sí, pero ten cuidado. Mejor solo cachear las arbitradas para que Qwen pueda aprender nuevas variaciones.

**P: ¿Se cachean las búsquedas fallidas?**  
R: No, solo guardamos cuando hay un resultado ejecutable (comando/archivo encontrado).

**P: ¿Qué pasa después de 1 hora?**  
R: La caché expira automáticamente. Si hay empate nuevamente, Qwen se llama de nuevo y la caché se actualiza.

---

## 🚀 Siguiente Paso

¿Quieres que implemente estos cambios ahora? Responde:
- **"sí"** → Implemento los 4 cambios y pruebo
- **"no"** → Solo quería entender el problema
- **"parcial"** → Implementa solo ciertos cambios (especifica cuáles)
