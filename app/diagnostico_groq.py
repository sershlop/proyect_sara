import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()

import requests

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL_CODIGO = "llama-3.3-70b-versatile"  # el mismo que usa config.py

print(f"Key cargada: {'sí, termina en ...' + GROQ_API_KEY[-4:] if GROQ_API_KEY else 'NO — vacía'}")

if not GROQ_API_KEY:
    print("La API key no se está leyendo desde el .env. Revisa el nombre exacto de la variable y la ubicación del archivo.")
    raise SystemExit(1)

prompt = (
    'Eres un generador de código. Devuelve SOLO un JSON con este formato exacto, '
    'sin texto adicional, sin markdown, sin backticks: '
    '{"codigo": "<codigo fuente completo>", "lenguaje": "<nombre del lenguaje>", "extension": "<.ext>"}\n\n'
    'Petición del usuario: "crea un script para sumar dos numeros"\n'
    'Si no se especifica lenguaje, usa Python por defecto.'
)

try:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL_CODIGO,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 700,
        },
        timeout=30,
    )
    print(f"Status code: {r.status_code}")
    print("Respuesta cruda completa:")
    print(r.text)
    r.raise_for_status()
    contenido = r.json()["choices"][0]["message"]["content"]
    print("\n--- Contenido del mensaje ---")
    print(contenido)
    print("\n--- Intento de parseo JSON ---")
    print(json.loads(contenido))
except Exception:
    print("\n--- EXCEPCIÓN COMPLETA ---")
    traceback.print_exc()