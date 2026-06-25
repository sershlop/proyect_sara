# 🔧 Solución Técnica: Código ANTES vs DESPUÉS

## 📝 Cambio 1: En `brain.py` - Consultar caché al inicio

### ❌ ANTES (línea ~580 en procesar())
```python
def procesar(texto_original):

    # ── Intención con destino — verificar ANTES de normalizar ─────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino

    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")
    

    candidatos = []
    texto_limpio = normalizar_texto(texto_original)

    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")

    # ── Intención con destino (abre X en Y) ───────────────────────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino
    
    # Capa -1 — Mapeo directo de comandos sistema conocidos
    for fila in MAPA_COMANDOS_SISTEMA.items():
        # ...
```

### ✅ DESPUÉS (línea ~580)
```python
def procesar(texto_original):

    # ── Intención con destino — verificar ANTES de normalizar ─────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino

    texto_limpio = normalizar_texto(texto_original)
    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")
    
    # ────────────────────────────────────────────────────────────
    # 🆕 NUEVO: Consultar caché ANTES de procesar
    # ────────────────────────────────────────────────────────────
    from database import obtener_cache_intencion
    resultado_cached = obtener_cache_intencion(texto_limpio)
    if resultado_cached:
        logger.debug("brain", f"✓ Caché encontrada para '{texto_limpio}'")
        return resultado_cached
    # ────────────────────────────────────────────────────────────

    candidatos = []
    texto_limpio = normalizar_texto(texto_original)

    if not texto_limpio:
        return _resultado("desconocido", "No entendí nada, ¿puedes repetirlo?")

    # ── Intención con destino (abre X en Y) ───────────────────────
    resultado_destino = _resolver_intencion_con_destino(texto_original)
    if resultado_destino:
        return resultado_destino
    
    # Capa -1 — Mapeo directo de comandos sistema conocidos
    for fila in MAPA_COMANDOS_SISTEMA.items():
        # ...
```

---

## 📝 Cambio 2: En `sara.py` - Guardar caché cuando confirma (COMANDOS)

### ❌ ANTES (línea ~410)
```python
            # ── Aprendizaje post-arbitraje ────────────────────────────────
            if exito and resultado.get("arbitrado"):
                confirmar = io_manager.preguntar_si_no(
                    "¿Era esto lo que pediste? (lo guardaré para la próxima)"
                )
                if confirmar:
                    # Guardar la petición original como palabras clave del comando existente
                    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    
                    # Primero intentar actualizar palabras clave del comando existente
                    id_cmd = comando.get("id")
                    if id_cmd:
                        from database import agregar_palabras_clave_comando
                        agregar_palabras_clave_comando(id_cmd, palabras_clave)
                        logger.info("sara", f"Palabras clave agregadas a cmd id={id_cmd}: '{palabras_clave}'")
                    else:
                        resultado_ap = learning.aprender_comando(
                            comando.get("nombre", entrada_original),
                            palabras_clave,
                            comando.get("accion", ""),
                            comando.get("tipo", "app"),
                            f"Aprendido por arbitraje desde: '{entrada_original}'"
                        )
                        if resultado_ap.get("exito"):
                            logger.info("sara", f"Arbitraje guardado: '{entrada_original[:50]}'")
                        elif resultado_ap.get("accion") == "duplicada":
                            logger.info("sara", "Comando ya existe, entrada registrada como variación")
```

### ✅ DESPUÉS (línea ~410)
```python
            # ── Aprendizaje post-arbitraje ────────────────────────────────
            if exito and resultado.get("arbitrado"):
                confirmar = io_manager.preguntar_si_no(
                    "¿Era esto lo que pediste? (lo guardaré para la próxima)"
                )
                if confirmar:
                    # Guardar la petición original como palabras clave del comando existente
                    palabras_clave = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    
                    # Primero intentar actualizar palabras clave del comando existente
                    id_cmd = comando.get("id")
                    if id_cmd:
                        from database import agregar_palabras_clave_comando, guardar_cache_intencion
                        agregar_palabras_clave_comando(id_cmd, palabras_clave)
                        logger.info("sara", f"Palabras clave agregadas a cmd id={id_cmd}: '{palabras_clave}'")
                        
                        # 🆕 NUEVO: Guardar en caché para evitar Qwen futuro (1 hora)
                        guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
                        logger.info("sara", f"✓ Caché guardada: '{texto_mostrar}' → {resultado.get('tipo')}")
                    else:
                        resultado_ap = learning.aprender_comando(
                            comando.get("nombre", entrada_original),
                            palabras_clave,
                            comando.get("accion", ""),
                            comando.get("tipo", "app"),
                            f"Aprendido por arbitraje desde: '{entrada_original}'"
                        )
                        if resultado_ap.get("exito"):
                            logger.info("sara", f"Arbitraje guardado: '{entrada_original[:50]}'")
                            # 🆕 NUEVO: Guardar en caché el resultado aprendido
                            guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
                        elif resultado_ap.get("accion") == "duplicada":
                            logger.info("sara", "Comando ya existe, entrada registrada como variación")
                            # 🆕 NUEVO: Guardar en caché también en caso de duplicada
                            guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
```

---

## 📝 Cambio 3: En `sara.py` - Guardar caché cuando confirma (ARCHIVOS)

### ❌ ANTES (línea ~450)
```python
    # ── ARCHIVO CON CONFIRMACIÓN ──────────────────────────────────────────
    elif tipo == "archivo_confirmar":
        nombre_archivo = resultado.get("texto", "ese archivo")
        confianza      = resultado.get("confianza", 0.0)

        io_manager.mostrar_respuesta(
            f"Encontré '{nombre_archivo}' ({round(confianza*100, 1)}% de coincidencia). "
            f"¿Es esto lo que buscas?"
        )

        if io_manager.preguntar_si_no(""):
            cmd = resultado.get("comando")
            if cmd:
                from database import incrementar_acceso_archivo
                incrementar_acceso_archivo(cmd.get("accion", ""))
                resultado_cmd = commands.ejecutar_comando(cmd)
                if resultado_cmd.get("exito"):
                    palabras_clave_nuevas = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    id_cmd = cmd.get("id")
                    if id_cmd:
                        from database import agregar_palabras_clave_comando
                        agregar_palabras_clave_comando(id_cmd, palabras_clave_nuevas)
```

### ✅ DESPUÉS (línea ~450)
```python
    # ── ARCHIVO CON CONFIRMACIÓN ──────────────────────────────────────────
    elif tipo == "archivo_confirmar":
        nombre_archivo = resultado.get("texto", "ese archivo")
        confianza      = resultado.get("confianza", 0.0)

        io_manager.mostrar_respuesta(
            f"Encontré '{nombre_archivo}' ({round(confianza*100, 1)}% de coincidencia). "
            f"¿Es esto lo que buscas?"
        )

        if io_manager.preguntar_si_no(""):
            cmd = resultado.get("comando")
            if cmd:
                from database import incrementar_acceso_archivo, guardar_cache_intencion
                incrementar_acceso_archivo(cmd.get("accion", ""))
                resultado_cmd = commands.ejecutar_comando(cmd)
                if resultado_cmd.get("exito"):
                    palabras_clave_nuevas = f"{entrada_original}, {entrada_usuario or ''}".strip(", ")
                    id_cmd = cmd.get("id")
                    if id_cmd:
                        from database import agregar_palabras_clave_comando
                        agregar_palabras_clave_comando(id_cmd, palabras_clave_nuevas)
                        # 🆕 NUEVO: Guardar en caché la decisión confirmada
                        guardar_cache_intencion(texto_mostrar, resultado, ttl_segundos=3600)
                        logger.info("sara", f"✓ Caché guardada: archivo '{nombre_archivo}'")
```

---

## 📝 Cambio 4: Integración en Importes de `brain.py`

### En la sección de importes (línea 1-20)
No hay cambios en importes porque `obtener_cache_intencion` se importa donde se usa.

---

## 📝 Cambio 5: Integración en Importes de `sara.py`

El import se hace donde se necesita (ya mostrado arriba).

---

## 📊 Resumen de Cambios

| Archivo | Ubicación | Tipo | Descripción |
|---------|-----------|------|-------------|
| `brain.py` | Línea ~585-590 | Agregar | Consultar caché al inicio de `procesar()` |
| `sara.py` | Línea ~420 | Agregar | Guardar caché después de guardar palabras clave (comando) |
| `sara.py` | Línea ~430 | Agregar | Guardar caché en aprendizaje nuevo |
| `sara.py` | Línea ~460 | Agregar | Guardar caché en confirmación de archivos |

---

## ✅ Validación Post-Cambios

Después de implementar, prueba así:

```
1. SARA: "Hola"
2. Tú: "abre notion"
3. SARA: 🔍 [DEBUG][brain] Total candidatos al ranking: 2
         ℹ️  [INFO][brain] Empate (0.02) → árbitro Qwen
         ...
         ℹ️  [INFO][external] Árbitro Qwen eligió...
         SARA: ¿Era esto lo que pediste?
4. Tú: "si"
5. SARA: ✓ Caché guardada: 'abre notion' → comando

─────────────────────────────────────────────

6. Tú: "abre notion" (nuevamente, 5 seg después)
7. SARA: 🔍 [DEBUG][brain] ✓ Caché encontrada para 'abre notion'
         ℹ️  [INFO][commands] App abierta: abrir_notion
         → SIN LLAMAR A QWEN ✅

8. Tú: "abre notion" (nuevamente, 30 min después)
9. SARA: (sin caché pero palabras clave están guardadas)
         → Si hay empate, QWEN se llama de nuevo
         → Pero después se cachea nuevamente

✅ El ciclo se rompe: SARA recuerda tus decisiones
```

---

## 🎯 Beneficios

1. ✅ **Evita Qwen repetitivo**: No llama a Qwen mientras la caché esté vigente
2. ✅ **Más rápido**: Segunda búsqueda es instantánea
3. ✅ **Usa lo aprendido**: Consulta la caché primero
4. ✅ **TTL configurable**: 1 hora (3600 seg) se puede ajustar
5. ✅ **Palabra clave + Caché**: Ambas estrategias trabajan juntas

---

## ⚠️ Consideraciones

- La caché expira después de 1 hora (configurable)
- Se almacena en `cache_intenciones` con `texto_limpio` normalizado
- Si el usuario dice algo ligeramente diferente ("abre notion" vs "abrir notion"), NO estará en caché
- Las palabras clave siguen siendo útiles para futuras búsquedas después que la caché expire
