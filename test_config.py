#!/usr/bin/env python3
"""
Script de prueba para verificar la configuración y migración automática
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core import settings

def test_config():
    print("=== Prueba de configuración ===")
    
    # Inicializar configuración
    settings.startup_check()
    
    # Crear un canal de prueba
    test_channel = "123456789"
    
    print(f"Probando configuración para canal: {test_channel}")
    
    try:
        # Leer configuración (esto debería crear el archivo si no existe)
        config = settings.read(test_channel)
        
        print("Configuración leída exitosamente:")
        for key, value in config.items():
            print(f"  {key}: {value}")
        
        # Verificar que distilled_cfg_scale esté presente
        if 'distilled_cfg_scale' in config:
            print(f"✅ distilled_cfg_scale encontrado: {config['distilled_cfg_scale']}")
        else:
            print("❌ distilled_cfg_scale NO encontrado")
            
        # Verificar que live_preview esté presente
        if 'live_preview' in config:
            print(f"✅ live_preview encontrado: {config['live_preview']}")
        else:
            print("❌ live_preview NO encontrado")
            
        # Verificar que upscaler_1 esté presente
        if 'upscaler_1' in config:
            print(f"✅ upscaler_1 encontrado: {config['upscaler_1']}")
        else:
            print("❌ upscaler_1 NO encontrado")
            
    except Exception as e:
        print(f"❌ Error al leer configuración: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_config() 