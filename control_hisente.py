# 📁 control_hisense_real.py
import asyncio
import sys
from androidtvremote2 import AndroidTVRemote, CannotConnect

IP_TV = "192.168.100.14"

async def emparejar_y_controlar():
    print("="*60)
    print("     SARA v0.4.0 — CLIENTE SEGURO GOOGLE TV (HISENSE)   ")
    print("="*60)
    print(f"[i] Conectando de forma segura a la IP: {IP_TV}...")
    
    # CORRECCIÓN: El argumento correcto de la librería es 'ip'
    remote = AndroidTVRemote(client_name="SARA_Agent", ip=IP_TV)
    
    # Callback opcional si la tele pide PIN en pantalla
    def cert_developer_pin():
        print("\n" + "!"*50)
        print("🧠 SARA DETECTÓ PROTOCOLO DE SEGURIDAD:")
        pin = input("Ingresa el código PIN que apareció en tu pantalla Hisense: ").strip()
        print("!"*50 + "\n")
        return pin

    try:
        # Intentamos iniciar el flujo seguro de Google TV
        await remote.async_connect(cert_developer_pin)
        print("[✓] ¡Conexión segura establecida con la televisión!")
        
    except CannotConnect:
        print(f"[x] Error crítico: No se pudo conectar a {IP_TV}. Verifica si es la TV correcta.")
        return
    except Exception as e:
        print(f"[x] Fallo en el handshake de Google TV: {e}")
        return

    while True:
        print("\n" + "-"*50)
        print("  1. 🔊 Subir Volumen (+)")
        print("  2. 🔉 Bajar Volumen (-)")
        print("  3. 🔇 Silenciar / Mute")
        print("  4. ❌ Salir")
        print("-"*50)
        
        opcion = input("Sergio, selecciona una acción (1-4): ").strip()
        
        try:
            if opcion == "1":
                print("[>>] Enviando comando: Volumen +")
                await remote.async_send_key("VOLUME_UP", "SHORT")
            elif opcion == "2":
                print("[>>] Enviando comando: Volumen -")
                await remote.async_send_key("VOLUME_DOWN", "SHORT")
            elif opcion == "3":
                print("[>>] Enviando comando: Mute")
                await remote.async_send_key("MUTE", "SHORT")
            elif opcion == "4":
                print("[i] Desconectando de la televisión de forma segura...")
                break
            else:
                print("[-] Opción inválida.")
        except Exception as e:
            print(f"[x] Error al despachar el comando por PRAXIS: {e}")
            break

if __name__ == "__main__":
    try:
        asyncio.run(emparejar_y_controlar())
    except KeyboardInterrupt:
        print("\n[i] Módulo finalizado por el usuario.")