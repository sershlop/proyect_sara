# test_imports.py
import sys
import os

modulos_necesarios = [
    'utils', 'app_actions', 'config', 'logger', 'database',
    'intent_engine', 'shell', 'productivity', 'file_index', 'app_index',
    'habitos', 'web_navigator', 'memoria_core', 'system_info', 'salud_sistema'
]

print("Testing imports...\n")
for mod in modulos_necesarios:
    try:
        __import__(mod)
        print(f"✅ {mod}")
    except ImportError as e:
        print(f"❌ {mod}: {e}")
    except Exception as e:
        print(f"⚠️  {mod}: {type(e).__name__}: {e}")
        