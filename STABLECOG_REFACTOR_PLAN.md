# StableCog Refactor Plan

## 1) Objetivo
Convertir `core/stablecog.py` (archivo monolitico) en una arquitectura modular, testeable y predecible, sin perder compatibilidad con los comandos actuales (`/draw`, `infinite`, batch, adetailer, highres, img2img/txt2img).

## 2) Problemas actuales
1. Demasiadas responsabilidades en una sola clase/archivo.
2. Alto acoplamiento entre validacion, construccion de payload, IO de red, post-procesado y respuesta Discord.
3. Manejo de errores no uniforme y dificil de probar por unidad.
4. Flujos complejos (Details++, live preview, batch grids) mezclados en el flujo principal.
5. Duplicacion o dispersion de logica de backend/options.
6. Dificultad para introducir cambios sin riesgo de regresion.

## 3) Arquitectura objetivo
Separar por capas y por responsabilidad:

1. `application` (orquestacion):
- recibe el comando y coordina el pipeline.
- no contiene reglas de payload detalladas.

2. `domain` (reglas):
- parseo/normalizacion de parametros.
- politicas de batch, estilos, extra_nets, defaults por canal.
- reglas de backend (automatic/forge/sdnext) y capacidades.

3. `infrastructure` (IO):
- cliente WebUI (`txt2img`, `img2img`, `options`, `png-info`, `progress`).
- repositorio de archivos para outputs.
- adaptador Discord para mensajes/posts/views.

4. `presentation`:
- mensajes de usuario, embeds, truncado de textos.
- formato final para queue/post.

## 4) Modulos propuestos
1. `core/stable/command_handler.py`
- entrada principal de `/draw`.
- llama a casos de uso.

2. `core/stable/request_parser.py`
- normaliza argumentos del slash command.
- aplica defaults por canal.

3. `core/stable/prompt_service.py`
- random prompt, prompt mod, estilos y extra nets.

4. `core/stable/backend_options.py`
- deteccion backend.
- construccion de `/options`.

5. `core/stable/payload_builder.py`
- construye payloads `txt2img`/`img2img`.
- agrega ADetailer/controlnet/highres/override settings.

6. `core/stable/webui_client.py`
- encapsula requests/sesiones/timeouts/retries y errores tipados.

7. `core/stable/detailspp_pipeline.py`
- flujo especifico de Details++ y restore de options.

8. `core/stable/postprocess.py`
- metadata PNG, batch grids, correccion de color, nombres de archivo.

9. `core/stable/discord_publisher.py`
- envio de progreso, mensajes finales, manejo de payload too large.

10. `core/stable/models.py`
- dataclasses para `DrawRequest`, `DrawRuntime`, `DrawResult`, `BackendOptions`.

## 5) Flujo objetivo (alto nivel)
1. Command -> parse request.
2. Validate request.
3. Build domain request (inmutable).
4. Prepare backend options.
5. Execute generation (`txt2img` o `img2img`).
6. Optional branch Details++.
7. Postprocess (files, grids, metadata, color fix).
8. Publish results to Discord.
9. Finalize queue siempre en `finally`.

## 6) Contratos recomendados
1. `DrawRequest`:
- todos los parametros ya normalizados y tipados.

2. `DrawResult`:
- lista de imagenes en memoria/base64.
- metadata por imagen.
- tiempos y datos de modelo.

3. `PublishResult`:
- mensajes/embeds/files listos para `queuehandler.process_post`.

## 7) Estrategia de errores y logging
1. Excepciones tipadas:
- `ValidationError`, `WebUIConnectionError`, `WebUIProtocolError`, `PostProcessError`.

2. Logging estandar:
- `logger.info` para eventos de negocio.
- `logger.warning` para degradaciones recuperables.
- `logger.exception` para fallos no recuperables.

3. Nunca bloquear cola:
- `queue_object.is_done = True` y `GlobalQueue.process_queue()` en `finally`.

## 8) Plan de migracion por fases

## Fase 0 - Congelamiento y seguridad
1. Agregar pruebas de smoke para `/draw` basico, `img2img`, batch, Details++.
2. Capturar comportamiento actual esperado (golden outputs de mensajes/payload clave).

## Fase 1 - Extraccion sin cambios funcionales
1. Mover parse/defaults de `dream_handler` a `request_parser.py`.
2. Mover armado de payload a `payload_builder.py`.
3. Mover logica backend options a `backend_options.py`.
4. Mantener `stablecog.py` como facade/orquestador.

## Fase 2 - Cliente WebUI dedicado
1. Crear `webui_client.py` con metodos tipados.
2. Unificar manejo de errores de red y respuestas JSON invalidas.
3. Eliminar monkeypatch global de `requests` y reemplazar por filtro local en client.

## Fase 3 - Details++ aislado
1. Extraer flujo Details++ a `detailspp_pipeline.py`.
2. Asegurar restore de options en `try/finally`.
3. Agregar pruebas para restore correcto aun con excepcion intermedia.

## Fase 4 - Publicacion y progreso
1. Extraer live preview y update loop a `discord_publisher.py`.
2. Eliminar logica de publicacion desde `dream()`.
3. Dejar contratos claros de entrada/salida.

## Fase 5 - Limpieza final
1. Reducir `stablecog.py` a coordinador de casos de uso.
2. Eliminar imports no usados, comentarios legacy y codigo muerto.
3. Documentar APIs internas de modulos nuevos.

## 9) Testing minimo recomendado
1. Unit tests:
- parser/defaults.
- payload builder por combinaciones (txt2img/img2img/highres/adetailer).
- backend options por `automatic/forge/sdnext`.

2. Integration tests:
- mock de WebUI client.
- salida de mensajes Discord esperados.

3. Regression tests:
- batch grid generation.
- Details++ option restore.
- queue finalize en errores.

## 10) Definition of Done
1. `stablecog.py` <= 300 lineas.
2. Cobertura de pruebas en modulos nuevos >= 70%.
3. Sin `print`, solo logger estandar.
4. Sin monkeypatch global de `requests`.
5. Sin regresiones funcionales en comandos existentes.

## 11) Orden de implementacion sugerido
1. Fase 1.
2. Fase 2.
3. Fase 3.
4. Fase 4.
5. Fase 5.

