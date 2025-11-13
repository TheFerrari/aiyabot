# Guía de Solución de Problemas - AIYA Bot

## Problemas Comunes y Soluciones

### 1. Error de Conexión con la Web UI

**Síntomas:**
```
Error de conexión al intentar autenticarse con http://127.0.0.1:7860
Invalid URL '/sdapi/v1/cmd-flags': No scheme supplied
```

**Soluciones:**

#### A. Verificar que la Web UI esté ejecutándose
1. Abre una terminal/cmd
2. Navega al directorio de Stable Diffusion Web UI
3. Ejecuta: `python launch.py` o `webui.bat`
4. Espera a que aparezca "Running on local URL: http://127.0.0.1:7860"

#### B. Verificar la URL en config.toml
1. Abre el archivo `config.toml`
2. Asegúrate de que la línea `url = "http://127.0.0.1:7860"` esté correcta
3. Si usas un puerto diferente, actualiza la URL

#### C. Verificar firewall y antivirus
1. Asegúrate de que el puerto 7860 no esté bloqueado
2. Agrega una excepción para la Web UI en tu antivirus

### 2. Error de Configuración Faltante

**Síntomas:**
```
KeyError: 'distilled_cfg_scale'
```

**Solución:**
- El bot ahora incluye migración automática de configuración
- Si persiste el error, ejecuta el script de diagnóstico:
  ```bash
  python diagnose_webui.py
  ```

### 3. Error de Live Preview

**Síntomas:**
```
Error en update_progress: [error específico]
```

**Soluciones:**

#### A. Deshabilitar Live Preview temporalmente
1. Usa el comando: `/settings live_preview:False`
2. Esto deshabilitará las actualizaciones en tiempo real

#### B. Verificar configuración de canal
1. Usa `/settings current_settings:True` para ver la configuración actual
2. Verifica que `live_preview` esté configurado correctamente

### 4. Error de Modelo No Encontrado

**Síntomas:**
```
Error al configurar el modelo: [error específico]
```

**Soluciones:**

#### A. Verificar que el modelo esté cargado
1. En la Web UI, ve a la pestaña "Settings"
2. Verifica que el modelo esté en la lista de modelos disponibles
3. Si no está, descárgalo o muévelo a la carpeta correcta

#### B. Actualizar lista de modelos
1. Usa el comando: `/settings refresh:True`
2. Esto actualizará la lista de modelos disponibles

### 5. Error de Memoria Insuficiente

**Síntomas:**
```
CUDA out of memory
```

**Soluciones:**

#### A. Reducir el tamaño de imagen
1. Usa tamaños más pequeños (512x512, 768x768)
2. Evita usar highres_fix con imágenes muy grandes

#### B. Reducir batch size
1. Usa batch size de 1
2. Reduce el número de pasos

#### C. Configurar la Web UI para usar menos memoria
1. Agrega `--medvram` o `--lowvram` al lanzar la Web UI
2. Usa `--xformers` para optimizar la memoria

## Scripts de Diagnóstico

### 1. Diagnóstico de Web UI
```bash
python diagnose_webui.py
```
Este script verifica:
- Configuración de URL
- Conectividad con la Web UI
- Estado de los endpoints de la API

### 2. Prueba de Configuración
```bash
python test_config.py
```
Este script verifica:
- Configuración de canales
- Migración automática de configuraciones
- Presencia de claves requeridas

## Configuración Recomendada

### config.toml básico
```toml
# URL de la Web UI
url = "http://127.0.0.1:7860"

# Configuración de autenticación (si es necesaria)
user = ""
pass = ""
apiuser = ""
apipass = ""

# Configuración de guardado
save_outputs = "True"
dir = "outputs"

# Límites
queue_limit = 99
max_size = 1536

# Configuración por defecto
steps = 30
max_steps = 50
width = 1024
height = 1024
guidance_scale = "5.0"
distilled_cfg_scale = "3.5"
sampler = "DPM++ 3M SDE"
scheduler = "exponential"
live_preview = true
```

## Comandos Útiles

### Configuración
- `/settings current_settings:True` - Ver configuración actual
- `/settings refresh:True` - Actualizar listas de modelos/samplers
- `/settings live_preview:False` - Deshabilitar live preview

### Información
- `/info` - Información sobre una imagen
- `/models` - Lista de modelos disponibles

## Logs y Debugging

### Habilitar logs detallados
1. Ejecuta el bot con: `python start_bot.py --debug`
2. Revisa los logs para identificar errores específicos

### Verificar logs de la Web UI
1. Revisa la consola donde ejecutaste la Web UI
2. Busca errores relacionados con la API

## Contacto y Soporte

Si los problemas persisten:
1. Ejecuta los scripts de diagnóstico
2. Revisa los logs del bot y la Web UI
3. Verifica que todas las dependencias estén actualizadas
4. Asegúrate de que la Web UI esté en una versión compatible 