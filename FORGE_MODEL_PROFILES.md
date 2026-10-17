# Perfiles de modelos de Forge Neo

AIYA puede usar checkpoints SDXL y Anima en `/draw`, img2img, hires y los
botones de regeneración. El preset se consulta por la API: no basta con
cambiar `forge_preset`, porque sus controles de Gradio no se ejecutan por REST.

## Configuración

1. En Forge Neo, configura los presets `xl` y `anima`, incluyendo los módulos
   de cada uno. Anima necesita su VAE y codificador de texto. En esta máquina
   están instalados `qwen_image_vae.safetensors` y `qwen_3_06b_base.safetensors`.
   SDXL puede usar módulos vacíos si el checkpoint ya incorpora sus componentes.
2. Edita `model_profiles.json`. El archivo incluido registra `anima_baseV10_txt`
   como Anima y conserva XL como arquitectura de los modelos no registrados.
   Añade explícitamente cada nuevo checkpoint Anima, incluidas sus variantes.
   No se deduce la arquitectura del nombre ni de `forge_checkpoint_anima`:
   este último solo representa la selección guardada de la interfaz.
3. Reinicia AIYA después de instalar esta versión. `AIYA_SD_BACKEND=forge` es
   recomendable; sin esa variable se detecta Forge por sus opciones API.
   Un valor explícito `automatic` o `sdnext` impide usar los perfiles Anima.
   Las credenciales siguen siendo las de la configuración existente.

Los modelos registrados que estén presentes en `/sdapi/v1/sd-models` se añaden
al selector del bot aunque no figuren en el archivo local `resources/models.csv`.
Se aceptan nombre del modelo, archivo o título con hash. Los nombres ambiguos
se rechazan. Los checkpoints y módulos deben existir en el servidor de Forge.

Para otra ubicación de configuración, define `AIYA_MODEL_PROFILES=/ruta/perfiles.json`.
Ejemplo con ajustes opcionales por checkpoint:

```json
{
  "default_architecture": "xl",
  "models": {
    "anima_baseV10_txt": {
      "architecture": "anima",
      "modules": ["qwen_image_vae.safetensors", "qwen_3_06b_base.safetensors"],
      "dtype": "Automatic",
      "parameters": {"steps": 32, "guidance_scale": 4, "distilled_cfg_scale": 3},
      "hires": {"hr_second_pass_steps": 16},
      "inherit_prompt_defaults": false
    }
  }
}
```

`modules`, `dtype`, `parameters`, `hires` e `inherit_prompt_defaults` son
opcionales. Por defecto se usan los módulos y dtype del preset de Forge.
Usa rutas completas del servidor si dos módulos tienen el mismo nombre.
Una ruta completa ausente se rechaza; no se sustituye por otro módulo.

## Parámetros y repetición

La prioridad es: argumento explícito de `/draw`, ajuste del checkpoint,
preset de Forge, valor del canal. Se respetan los límites de pasos del canal:
si `max_steps` es 28, un preset de 32 pasos se limita a 28. Las dimensiones
`0` del preset conservan las del canal. Las dimensiones deben ser múltiplos de
64, y el tamaño final de hires se redondea a ese múltiplo.

Para Anima, `distilled_cfg_scale` representa **Shift**. El preset instalado
usa ER SDE / Beta, 32 pasos, CFG 4 y Shift 3. Se consultan por separado los
valores `anima_t2i_*`, `anima_i2i_*` y `anima_t2i_hr_*`. Un argumento explícito
de pasos, CFG o Shift también se aplica a hires, salvo que `hires` tenga un
ajuste específico en el perfil del checkpoint.

Cada tarea guarda una copia del perfil resuelto. Los botones de semilla,
regeneración y variantes conservan ese perfil sin volver a consultar los
presets. Al editar y cambiar de modelo, los campos que no se cambiaron en el
modal se resuelven con el nuevo perfil. También se guarda el perfil en el
campo PNG `aiya_profile`; los metadatos normales conservan sampler, scheduler
y Shift y permiten copiar el comando desde `/meta` o la información de imagen.
Importar un PNG resuelve los módulos usando la configuración actual del bot;
no ejecuta las rutas de módulos guardadas en un PNG externo.

El perfil Anima omite por defecto el prefijo de prompt, LoRA, hypernet y estilo
automáticos del canal para evitar heredar ajustes de XL. Se pueden pasar LoRAs
y estilos explícitos. Se conservan las reglas de moderación y el prefijo
negativo. CLIP skip no se envía a Anima. Hires y ADetailer se activan de forma
explícita para Anima.

## API y compatibilidad

Se consultan `/sdapi/v1/options`, `/sdapi/v1/sd-modules`, `/sdapi/v1/sd-models`,
`/sdapi/v1/samplers` y `/sdapi/v1/schedulers`. Los resultados se guardan durante
60 segundos y se actualizan con la recarga de listas. Si Forge está temporalmente
inaccesible, se usa la última copia válida de ese servidor. Sin una copia válida,
un error de conexión se muestra antes de encolar.

Checkpoint, módulos y dtype se envían juntos en `override_settings` con
`override_settings_restore_afterwards=true`. No se modifica el preset guardado
ni se escriben opciones globales para cada generación de Forge. Al pasar de
Anima a XL se especifican los módulos XL, incluso cuando su lista está vacía.

La primera versión de perfiles admite `xl` y `anima`. Otros backends conservan
su recorrido existente cuando se configuran explícitamente. Se requiere Forge
Neo con los endpoints anteriores y soporte Anima; el Forge original no expone
necesariamente esos contratos.

ADetailer usa el modelo, sampler y CFG de la generación. La extensión instalada
construye su pase de inpainting con el Shift predeterminado del backend (3.5),
sin un control propio para heredarlo del pase base; los perfiles de AIYA no
cambian esa implementación externa.

## Validación

Con las dependencias del bot instaladas:

```sh
python -m unittest discover -s tests -v
python -m compileall -q core
```

Las pruebas usan mocks y no envían mensajes a Discord ni generan imágenes.
Cubren prioridad de valores, módulos, cambio XL → Anima → XL, presets de img2img
y hires, errores, copia de perfiles, compatibilidad de tuplas antiguas y el
payload real construido por la cola del bot.

Para comprobar una instalación real, generar sucesivamente con XL, Anima y XL
con la misma semilla, y después ejecutar img2img, hires y ADetailer con Anima.
Comprobar imagen y metadatos, y comparar las opciones globales antes y después
de cada petición. Un HTTP 200 por sí solo no demuestra que el VAE sea correcto.

### Resultado en esta PC (17 de octubre de 2026)

Forge Neo `neo-2.30`, commit `af59a359`, RTX 3080 Ti:

| Petición | Tamaño | Resultado |
|---|---|---|
| XL antes | 512×512 | Imagen válida |
| Anima, preset de 32 pasos | 1024×1024 | Imagen válida, Shift 3, módulos Qwen |
| XL después | 512×512 | Mismos píxeles que XL antes, misma semilla |
| Anima img2img | 512×512 | Imagen válida, fuerza 0.5 |
| Anima hires | 768×768 | Imagen válida, Shift de hires 3 |
| Anima ADetailer | 512×512 | Imagen válida, detector de rostro ejecutado |

Las opciones de checkpoint, módulos, dtype, preset y referencia Anima se
restauraron después de las seis peticiones. Se revisaron visualmente las
imágenes Anima y se comprobaron sus metadatos. Estas peticiones validan Forge;
las pruebas automatizadas con mocks validan la integración del comando y la
cola sin publicar mensajes en Discord. El servicio del bot mantiene su versión
anterior hasta que se despliegue esta rama y se reinicie.
