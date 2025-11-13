#!/usr/bin/env python3
"""
Script de inicio mejorado para AIYA Bot
Maneja errores de conexión y proporciona información clara sobre el estado del bot
"""

import os
import sys
import time
import subprocess
from pathlib import Path

def check_python_version():
    """Verificar que la versión de Python sea compatible"""
    if sys.version_info < (3, 8):
        print("❌ Error: Se requiere Python 3.8 o superior")
        print(f"   Versión actual: {sys.version}")
        return False
    print(f"✅ Python {sys.version.split()[0]} - Compatible")
    return True

def check_requirements():
    """Verificar que los archivos de requisitos existan"""
    requirements_files = ['requirements.txt', 'requirements_no_generate.txt']
    for req_file in requirements_files:
        if not os.path.exists(req_file):
            print(f"⚠️  Advertencia: {req_file} no encontrado")
        else:
            print(f"✅ {req_file} encontrado")
    return True

def check_config_files():
    """Verificar archivos de configuración esenciales"""
    essential_files = [
        'aiya.py',
        'core/settings.py',
        'config.toml',
        'models.csv'
    ]
    
    missing_files = []
    for file_path in essential_files:
        if not os.path.exists(file_path):
            missing_files.append(file_path)
        else:
            print(f"✅ {file_path} encontrado")
    
    if missing_files:
        print(f"⚠️  Archivos faltantes: {', '.join(missing_files)}")
        return False
    
    return True

def check_webui_status():
    """Verificar si la Web UI de Stable Diffusion está ejecutándose"""
    import requests
    try:
        response = requests.get("http://127.0.0.1:7860/sdapi/v1/cmd-flags", timeout=5)
        if response.status_code == 200:
            print("✅ Web UI de Stable Diffusion detectada y funcionando")
            return True
        else:
            print(f"⚠️  Web UI responde pero con código de estado: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("⚠️  Web UI de Stable Diffusion no está ejecutándose")
        print("   El bot funcionará con funcionalidad limitada")
        return False
    except requests.exceptions.Timeout:
        print("⚠️  Timeout al conectar con la Web UI")
        print("   El bot funcionará con funcionalidad limitada")
        return False
    except Exception as e:
        print(f"⚠️  Error al verificar Web UI: {e}")
        return False

def install_requirements():
    """Instalar dependencias si es necesario"""
    print("\n📦 Verificando dependencias...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], 
                      check=True, capture_output=True, text=True)
        print("✅ Dependencias instaladas correctamente")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error al instalar dependencias: {e}")
        print("   Intenta ejecutar manualmente: pip install -r requirements.txt")
        return False

def start_bot():
    """Iniciar el bot"""
    print("\n🚀 Iniciando AIYA Bot...")
    print("=" * 50)
    
    try:
        # Importar y ejecutar el bot
        import aiya
        print("✅ Bot iniciado correctamente")
    except KeyboardInterrupt:
        print("\n🛑 Bot detenido por el usuario")
    except Exception as e:
        print(f"❌ Error al iniciar el bot: {e}")
        print("   Revisa los logs para más detalles")
        return False
    
    return True

def main():
    """Función principal"""
    print("🤖 AIYA Bot - Verificador de Inicio")
    print("=" * 50)
    
    # Verificaciones previas
    if not check_python_version():
        return False
    
    if not check_requirements():
        print("⚠️  Algunos archivos de requisitos faltan")
    
    if not check_config_files():
        print("❌ Faltan archivos de configuración esenciales")
        return False
    
    # Verificar Web UI
    print("\n🔍 Verificando Web UI de Stable Diffusion...")
    webui_running = check_webui_status()
    
    if not webui_running:
        print("\n📋 Para usar todas las funciones del bot:")
        print("   1. Instala y ejecuta la Web UI de Stable Diffusion")
        print("   2. Asegúrate de que esté ejecutándose en http://127.0.0.1:7860")
        print("   3. Reinicia el bot")
        print("\n   El bot funcionará con funcionalidad limitada por ahora.")
    
    # Preguntar si instalar dependencias
    print("\n" + "=" * 50)
    response = input("¿Deseas instalar/actualizar las dependencias? (y/N): ").lower().strip()
    if response in ['y', 'yes', 'sí', 'si']:
        if not install_requirements():
            print("❌ No se pudieron instalar las dependencias")
            return False
    
    # Iniciar el bot
    print("\n" + "=" * 50)
    response = input("¿Deseas iniciar el bot ahora? (Y/n): ").lower().strip()
    if response in ['', 'y', 'yes', 'sí', 'si']:
        return start_bot()
    else:
        print("👋 Para iniciar el bot manualmente, ejecuta: python aiya.py")
        return True

if __name__ == "__main__":
    success = main()
    if not success:
        print("\n❌ El bot no pudo iniciarse correctamente")
        print("   Revisa los errores anteriores y vuelve a intentar")
        input("\nPresiona Enter para salir...")
        sys.exit(1)
    else:
        print("\n✅ Proceso completado exitosamente") 