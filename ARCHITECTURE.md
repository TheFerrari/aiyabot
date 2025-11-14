# Arquitectura de AIYABot

Este documento resume la arquitectura actual del bot, mapea los comandos y funciones principales y propone una reorganización modular para facilitar nuevas capacidades.

---

## 1. Visión general

- **Dominio**: Bot de Discord para generación de imágenes (Stable Diffusion / AUTOMATIC1111 WebUI), upscale, interrogación de imágenes, generación de prompts, minijuego y chatbot LLM.
- **Tecnologías**:
  - `discord.py` (slash commands, message commands, views, modals).
  - API REST de AUTOMATIC1111 (`/sdapi/v1/...`).
  - LLMs vía `llama_cpp` / `transformers` (WizzGPTv6, Llama Vision).
  - Playwright (publicación en Civitai), ficheros locales (outputs, configs TOML/JSON, CSV).
- **Estructura actual**:
  - Fichero principal `aiya.py` que crea el bot, realiza checks de arranque y carga cogs.
  - Módulos en `core/` agrupados por funcionalidad (cogs, colas, vistas, configuración, utilidades).

---

## 2. Superficie de comandos y eventos

### 2.1 Slash commands

**Definidos en `aiya.py`:**

- `/stats` – Muestra número total de imágenes generadas.
- `/queue` – Muestra tamaños de las colas globales.
- `/ping` – Comprobación de latencia.

**Cogs en `core/`:**

- `StableCog` (`core/stablecog.py`)
  - `/draw` – Generación de imágenes (txt2img / img2img).
  - `/stopdraw` – Detiene generación infinita iniciada con `/draw`.
- `UpscaleCog` (`core/upscalecog.py`)
  - `/upscale` – Upscale de una imagen.
- `IdentifyCog` (`core/identifycog.py`)
  - `/identify` – Interrogación/descripcion de una imagen.
- `SettingsCog` (`core/settingscog.py`)
  - `/settings` – Configuración por canal (modelo, tamaño, batch, etc.).
- `InfoCog` (`core/infocog.py`)
  - `/info` – Listas de modelos/estilos/etc. y descarga de batches.
- `LeaderboardCog` (`core/leaderboardcog.py`)
  - `/leaderboard` – Muestra ranking de usuarios.
- `MetaCog` (`core/metacog.py`)
  - `/meta` – Extrae metadatos de una imagen y permite re-generar.
- `GenerateCog` (`core/generatecog.py`)
  - `/generate` – Genera prompts a partir de texto con LLM.
- `MinigameCog` (`core/minigamecog.py`)
  - `/minigame` – Minijuego de adivinar el prompt.

### 2.2 Message commands / context menus

Definidos en `aiya.py`, delegan en `core/ctxmenuhandler.py`:

- `"Get Image Info"` → `get_image_info(ctx, message)`.
- `"Quick Upscale"` → `quick_upscale(bot, ctx, message)`.
- `"Download Batch"` → `batch_download(ctx, message)`.

### 2.3 Comandos con prefijo `!` (texto)

En `core/chatbotcog.py` (`LlamaChatCog`):

- `!reset` – Reinicia el contexto del chatbot.
- `!stop` – Detiene la generación actual.
- `!generate` – Genera prompts de imagen vía LLM.

### 2.4 Eventos relevantes

- `aiya.py`: `on_ready`, `on_raw_reaction_add`, `on_guild_join`.
- Cogs: listeners `on_ready` que registran vistas (`StableCog`, `UpscaleCog`, `IdentifyCog`, `InfoCog`, `LeaderboardCog`, `LlamaChatCog`).

---

## 3. Mapa de módulos y funciones principales

### 3.1 Entrada y wiring (`aiya.py`)

- Crea `commands.Bot`, configura intents y logging (`core.logging_setup.get_logger`).
- Ejecuta `settings.startup_check()` y `settings.files_check()`.
- Carga extensiones: `core.settingscog`, `core.stablecog`, `core.upscalecog`, `core.identifycog`, `core.infocog`, `core.leaderboardcog`, `core.generatecog` (opcional según env), `core.chatbotcog`.
- Define comandos globales `/stats`, `/queue`, `/ping`, context menus y eventos de arranque.

### 3.2 Configuración y estado (`core/settings.py`, `core/settingscog.py`)

- `settings.py`:
  - Clase global `GlobalVar` con:
    - Config de conexión (`url`, auth API, rutas de salida).
    - Parámetros por defecto de generación (modelo, pasos, tamaños, sampler, scheduler, estilos, extra nets, etc.).
    - Listas de modelos, estilos, embeddings, loras, hypernets, upscalers, etc.
  - Funciones principales:
    - `batch_format`, `prompt_mod`, `extra_net_check`, `extra_net_dedup`, `extra_net_defaults`.
    - `queue_check`, `stats_count`, `messages*` (frases de cola).
    - `check`, `build`, `read`, `update` para config por canal.
    - `authenticate_user` (devuelve `requests.Session` autenticada).
    - `startup_check`, `check_webui_running`, `files_check`, `populate_global_vars`.
- `settingscog.py`:
  - `SettingsCog` con `/settings` y autocompletados (`model_autocomplete`, `sampler_autocomplete`, `scheduler_autocomplete`, etc.).
  - Aplica cambios a través de `settings.update` y valida límites (`max_steps`, `max_batch`, etc.).

### 3.3 Colas y vistas (`core/queuehandler.py`, `core/viewhandler.py`, `core/ctxmenuhandler.py`)

- `queuehandler.py`:
  - Objetos de trabajo: `DrawObject`, `DeforumObject`, `UpscaleObject`, `IdentifyObject`, `GenerateObject`, `PostObject`.
  - `ProgressView` con botones de interrupción.
  - Clase estática `GlobalQueue`:
    - Colas `queue`, `generate_queue`, `post_queue`.
    - Threads y event loops asociados.
    - `get_queue_sizes`, `create_progress_bar`, `update_progress_message`, `process_queue`.
  - Funciones: `process_dream`, `process_generate`, `process_post`.
- `viewhandler.py`:
  - `serialize_input_tuple` / `deserialize_input_tuple`.
  - Vistas y modals compartidos: `DrawModal`, `DrawView`, `DeleteView`, `DownloadMenu`, `UpscaleMenu`.
  - Interactúa con `GlobalQueue` y cogs de generación/upscale.
- `ctxmenuhandler.py`:
  - Parsing de metadatos PNG: `extra_net_search`, `style_search`, `style_remove`, `parse_image_info`.
  - Handlers para context menus: `get_image_info`, `quick_upscale`, `batch_download`.

### 3.4 Cogs de generación e imagen (`core/stablecog.py`, `core/upscalecog.py`, `core/identifycog.py`, `core/generatecog.py`, `core/metacog.py`, `core/infocog.py`)

- `stablecog.py` (StableCog):
  - `GPT2ModelSingleton` para cargar el modelo LLM local.
  - Slash commands `/draw` y `/stopdraw`.
  - Métodos `dream` y `post` que consumen `DrawObject` vía `GlobalQueue` y llaman a la WebUI (`/sdapi/v1/txt2img` / `img2img`), aplicando `apply_color_correction`.
- `upscalecog.py` (UpscaleCog):
  - Slash command `/upscale`, creación de `UpscaleObject` y consumo a través de `queuehandler`.
  - Llama a `/sdapi/v1/extra-single-image` y envía resultados a Discord.
- `identifycog.py` (IdentifyCog):
  - Slash command `/identify`, construcción de `IdentifyObject`.
  - Combina descarga robusta de imagen + llamada a `/sdapi/v1/interrogate`, actualización de leaderboard.
- `generatecog.py` (GenerateCog):
  - Slash command `/generate` para prompts de imagen.
  - Vistas (`GenerateView`, botones/selects) que permiten lanzar `/draw` sobre los prompts generados.
  - Usa LLM (vía `llama_cpp`/`transformers`) para extender textos.
- `metacog.py` (MetaCog):
  - Slash command `/meta` que extrae metadatos de PNG, construye un comando `/draw` equivalente y los presenta en un embed.
  - `MetaView` con botón para re-generar usando los parámetros extraídos.
- `infocog.py` (InfoCog):
  - Slash command `/info`:
    - Modo UI con `InfoView` (botones para modelos, estilos, loras, embeddings, wildcards…).
    - Modo descarga de imágenes por `batch_id`/`image_id`.

### 3.5 Otros cogs y utilidades (`core/leaderboardcog.py`, `core/chatbotcog.py`, `core/minigamecog.py`, utilidades)

- `leaderboardcog.py`:
  - CSV `leaderboard.csv` para contadores de uso (imágenes, identify, deforum, generate, chat).
  - `update_leaderboard(user_id, username, action)` y `/leaderboard`.
- `chatbotcog.py` (LlamaChatCog):
  - Inicializa modelo LLM (Llama Vision / WizzGPT).
  - Gestiona conversación, comandos `!reset`, `!stop`, `!generate` y actualiza leaderboard.
- `minigamecog.py`:
  - Clase `Minigame` con lógica del juego (prompt objetivo, guesses, imágenes).
  - `MinigameView` y modals para continuar el juego y registrar respuestas.
  - `/minigame` para iniciar partidas, usando la misma infraestructura de generación que `/draw`.
- Otras utilidades:
  - `color_correction_sharpening.py` – `measure_saturation`, `apply_sharpening`, `apply_color_correction`.
  - `civitaiposter.py` – `post_image_to_civitai` y helpers de sesión Chrome.
  - `logging_setup.py` – configuración básica de logging.
  - `constants.py` – constantes de backend (`BACKEND_WEBUI`, `BACKEND_SDNEXT`).
  - `setup_generate.py` – descarga inicial de modelos LLM desde HuggingFace.

---

## 4. Flujos principales

- **Flujo `/draw`**:
  - `StableCog.dream_handler` valida parámetros y configura defaults con `settings`.
  - Crea `DrawObject` → encola en `GlobalQueue.queue` vía `queuehandler`.
  - Thread de generación ejecuta `StableCog.dream`, llama a la WebUI, guarda imagen y programa `post`.
  - `StableCog.post` envía el resultado a Discord y `GlobalQueue.process_queue` continúa la cola.

- **Flujo `/upscale`**:
  - `UpscaleCog.dream_handler` normaliza parámetros, crea `UpscaleObject` y lo pasa a `queuehandler`.
  - `UpscaleCog.dream` llama al endpoint de upscale, genera el archivo y usa `queuehandler.process_post`.

- **Flujo `/identify`**:
  - `IdentifyCog.dream_handler` recibe attachment/URL, crea `IdentifyObject`.
  - `IdentifyCog.dream` descarga la imagen, llama a `/sdapi/v1/interrogate`, construye embed y actualiza leaderboard.

- **Flujo `/generate` + redraw**:
  - `GenerateCog.generate_handler` llama al LLM para generar n prompts.
  - `GenerateView` presenta prompts y opciones; al pulsar un botón, se llama a `StableCog.dream_handler` con el prompt elegido.

- **Flujo de context menu "Quick Upscale"**:
  - `aiya.py` → `ctxmenuhandler.quick_upscale`, que extrae URL y configura un `UpscaleObject` para `queuehandler`.

---

## 5. Recomendaciones de arquitectura y modularización

1. **Separar capas**:
   - Cogs y vistas como capa de interfaz (Discord).
   - Servicios de dominio (generación, upscale, identify, minigame, prompts) sin dependencias de Discord ni de `requests`.
   - Clientes de infraestructura (WebUI, Lexica, Civitai, disco) encapsulados en módulos específicos.

2. **Reducir estado global**:
   - Sustituir `settings.global_var` por objetos `Config`/`ChannelSettings` pasados explícitamente a los servicios.
   - Encapsular `GlobalQueue` en un `QueueManager` instanciable, inyectado en los cogs.

3. **Dividir ficheros grandes**:
   - `settings.py`: separar configuración, helpers de prompt, startup checks y acceso a WebUI.
   - `stablecog.py`: extraer LLM y llamadas HTTP a cliente `StableDiffusionClient`.
   - `viewhandler.py` / `ctxmenuhandler.py`: mover vistas a `core/ui/` y parsing de metadatos a `core/stable/metadata.py`.

4. **Interfaces más claras para nuevas features**:
   - Definir interfaces como `ImageGenerationService`, `UpscaleService`, `PromptGenerationService`, `LeaderboardRepository`.
   - Nuevos comandos deberían apoyarse en estos servicios sin duplicar lógica.

5. **Evolución incremental**:
   - Introducir los nuevos servicios manteniendo funciones actuales como fachada (para no romper el bot).
   - Añadir tests unitarios centrados en los servicios de dominio y en el parsing de datos (prompts, metadatos, payloads).
