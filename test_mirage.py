# 📁 test_hisense.py
import socket
import requests

# 🔍 CONFIGURACIÓN: Reemplaza esto con la IP local de tu TV Hisense
IP_TELEVISION = "192.168.100.14"  # <--- Cambia esto por la IP de tu tele

def verificar_puertos_hisense(ip):
    """
    Escanea los puertos de control clave para Smart TVs Hisense
    bajo protocolos Android TV, Google Cast y el servicio nativo de Hisense.
    """
    # 36666: Puerto nativo Smart TV Hisense (WebSocket/HTTP Control)
    puertos_clave = {
        36666: "Hisense TV Service Protocol (API nativa de la marca)",
        8008: "Google Cast API (Habilitado para streaming e información)",
        6466: "Google TV Remote Protocol (Control de botones/teclado)",
        5555: "ADB / Android Debug Bridge (Si tu Hisense usa Android TV)",
        9000: "Puerto secundario de emparejamiento / SSL"
    }
    
    print(f"\n[SARA DIAGNÓSTICO] Escaneando puertos en Hisense TV ({ip})...")
    puertos_abiertos = []
    
    for puerto, descripcion in puertos_clave.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        resultado = sock.connect_ex((ip, puerto))
        if resultado == 0:
            print(f"  [✓] Puerto {puerto} ABIERTO: {descripcion}")
            puertos_abiertos.append(puerto)
        else:
            print(f"  [x] Puerto {puerto} cerrado/inaccesible.")
        sock.close()
        
    return puertos_abiertos

def probar_ping_api_hisense(ip):
    """
    Intenta una consulta HTTP rápida al puerto nativo de Hisense 
    para ver si la API responde de manera limpia.
    """
    print(f"\n[SARA DIAGNÓSTICO] Intentando handshake básico con API Hisense (Puerto 36666)...")
    url = f"http://{ip}:36666/remote/process/key"  # Endpoint común de control
    
    try:
        # Enviamos una petición de prueba con timeout corto
        respuesta = requests.get(f"http://{ip}:36666", timeout=2)
        print(f"  [INFO] Estado de respuesta de la API: {respuesta.status_code}")
        return True
    except requests.exceptions.RequestException:
        print("  [-] El puerto web 36666 no respondió a peticiones HTTP planas (común si requiere HTTPS/WS).")
        return False

def simular_flujo_praxis_sara(ip, puertos):
    """
    Analiza las respuestas de la red local para determinar cómo integrarlo 
    asíncronamente en el Subsistema PRAXIS de SARA v0.4.0.
    """
    print("\n" + "="*60)
    print("🧠 EVALUACIÓN DE CAPACIDADES DE SARA PARA TU HISENSE TV")
    print("="*60)
    
    if not puertos:
        print("🔴 DIAGNÓSTICO: La TV Hisense no tiene puertos de control abiertos.")
        print("   SARA RECOMIENDA: Enciende la TV, ve a Configuración -> Red/Sistema")
        print("   y busca activar 'Compartir Pantalla', 'Control por red' o 'Remoto virtual'.")
        print("   Si está blindada, usaremos la vía Infrarroja de bajo costo.")
        return
        
    if 36666 in puertos:
        print("🟢 EXCELENTE: Puerto nativo Hisense detectado (36666).")
        print("   SARA puede usar librerías como 'hisensetv' o enviar peticiones seguras.")
        print("   Comandos viables desde PRAXIS:")
        print("     -> Enviar comandos de apagado, control de volumen y cambio de input (HDMI).")
        print("     -> Nota: Modelos recientes de Hisense piden un código PIN en pantalla la primera vez.")
        
    elif 5555 in puertos:
        print("🟢 MODO ANDROID: Puerto ADB detectado (5555).")
        print("   Tu Hisense corre sobre Android TV puro.")
        print("   SARA puede simular pulsaciones usando comandos de consola directos (`adb shell input`).")
        
    elif 8008 in puertos or 6466 in puertos:
        print("规则 CAPACIDAD ESTÁNDAR: Protocolos de Google Cast disponibles.")
        print("   SARA podrá controlar la reproducción multimedia, pausar y lanzar apps de streaming.")
    else:
        print("🔵 CAPACIDAD RESTRINGIDA: La TV responde pero los puertos principales están cerrados.")

if __name__ == "__main__":
    print("="*60)
    print("      SARA v0.4.0 — MÓDULO EXPERIMENTAL DE DIAGNÓSTICO HISENSE     ")
    print("="*60)
    
    # 1. Comprobar que responda en la red
    print(f"[i] Conectando con Hisense en {IP_TELEVISION}...")
    try:
        socket.setdefaulttimeout(2)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((IP_TELEVISION, 80))
        print("[OK] Conexión física a nivel de red establecida.")
    except Exception:
        print("[!] Nota: Puerto web genérico inactivo, buscando puertos de control específicos...")

    # 2. Correr escaneos estructurales
    puertos_abiertos = verificar_puertos_hisense(IP_TELEVISION)
    probar_ping_api_hisense(IP_TELEVISION)
    
    # 3. Dar veredicto de diseño
    simular_flujo_praxis_sara(IP_TELEVISION, puertos_abiertos)