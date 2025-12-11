import os
import subprocess
import threading
from typing import Optional

import requests

_sd_process_lock = threading.Lock()
_sd_process: Optional[subprocess.Popen] = None
_requests_patched = False


def _get_sd_config_from_env():
    """
    Lee desde variables de entorno la carpeta y el .bat que arrancan la WebUI.

    Espera:
    - SD_FOLDER_PATH: ruta a la carpeta raíz de Stable Diffusion WebUI.
    - SD_START_BAT_FILE_NAME: nombre del .bat que inicia la WebUI.
    """
    folder = os.getenv("SD_FOLDER_PATH")
    bat_name = os.getenv("SD_START_BAT_FILE_NAME")

    if folder:
        folder = folder.strip().strip('"').strip("'")
    if bat_name:
        bat_name = bat_name.strip().strip('"').strip("'")

    return folder, bat_name


def is_sd_process_running() -> bool:
    """
    Indica si el último proceso lanzado por este módulo sigue vivo.

    Nota: sólo controla procesos iniciados por este archivo, no
    instancias de WebUI lanzadas manualmente fuera del bot.
    """
    global _sd_process
    with _sd_process_lock:
        if _sd_process is None:
            return False
        return _sd_process.poll() is None


def _start_sd_process_internal(folder: str, bat_name: str) -> bool:
    """
    Arranca el .bat de Stable Diffusion en la carpeta indicada.

    Devuelve True si se ha lanzado un nuevo proceso, False en caso contrario.
    """
    global _sd_process

    bat_path = os.path.join(folder, bat_name)
    if not os.path.isfile(bat_path):
        print(f"[auto_restart_sd] No se encontró el archivo .bat en: {bat_path}")
        return False

    try:
        if os.name == "nt":
            # Windows: lanzamos cmd para ejecutar el .bat.
            # Guardamos el PID del cmd que mantiene viva la WebUI.
            proc = subprocess.Popen(
                ["cmd", "/c", bat_path],
                cwd=folder,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            )
        else:
            # Otros sistemas: ejecuta el .bat / script directamente.
            proc = subprocess.Popen([bat_path], cwd=folder)

        with _sd_process_lock:
            _sd_process = proc

        print(f"[auto_restart_sd] Stable Diffusion arrancado (PID={proc.pid}) usando: {bat_path}")
        return True
    except Exception as e:
        print(f"[auto_restart_sd] Error al intentar iniciar Stable Diffusion: {e}")
        return False


def restart_sd_if_needed() -> bool:
    """
    Revisa si el proceso conocido de Stable Diffusion sigue activo.
    Si no lo está (o nunca se inició desde aquí), intenta ejecutar el .bat.

    Devuelve True si se ha iniciado un nuevo proceso, False si no.
    """
    folder, bat_name = _get_sd_config_from_env()
    if not folder or not bat_name:
        print(
            "[auto_restart_sd] Auto-reinicio deshabilitado. "
            "Configura SD_FOLDER_PATH y SD_START_BAT_FILE_NAME en tu .env."
        )
        return False

    if is_sd_process_running():
        # Ya tenemos un proceso vivo, no lanzamos otro.
        print("[auto_restart_sd] Stable Diffusion ya está en ejecución; no se reinicia.")
        return False

    return _start_sd_process_internal(folder, bat_name)


def _should_handle_url(url: str) -> bool:
    """
    Devuelve True si la URL parece pertenecer a la API de Stable Diffusion.

    Nos limitamos a endpoints que contengan 'sdapi' para no tocar otras
    llamadas HTTP que pueda hacer el bot.
    """
    try:
        return "sdapi" in str(url)
    except Exception:
        return False


_original_requests_post = requests.post
_original_session_post = requests.sessions.Session.post


def _patched_requests_post(url, *args, **kwargs):
    try:
        return _original_requests_post(url, *args, **kwargs)
    except requests.exceptions.ConnectionError:
        if _should_handle_url(url):
            print("[auto_restart_sd] ConnectionError hacia la API de Stable Diffusion. Intentando auto-reinicio...")
            try:
                restart_sd_if_needed()
            except Exception as e:
                print(f"[auto_restart_sd] Error al intentar auto-reiniciar (requests.post): {e}")
        raise


def _patched_session_post(self, url, *args, **kwargs):
    try:
        return _original_session_post(self, url, *args, **kwargs)
    except requests.exceptions.ConnectionError:
        if _should_handle_url(url):
            print("[auto_restart_sd] ConnectionError hacia la API de Stable Diffusion (Session). Intentando auto-reinicio...")
            try:
                restart_sd_if_needed()
            except Exception as e:
                print(f"[auto_restart_sd] Error al intentar auto-reiniciar (Session.post): {e}")
        raise


def patch_requests_once():
    """
    Aplica un monkeypatch ligero sobre requests para que, cuando se produzca
    un ConnectionError hacia /sdapi, intentemos relanzar la WebUI mediante
    restart_sd_if_needed().

    Se llama una sola vez para evitar parches repetidos.
    """
    global _requests_patched
    if _requests_patched:
        return

    requests.post = _patched_requests_post
    requests.sessions.Session.post = _patched_session_post
    _requests_patched = True


# Aplicar el parche en cuanto se importe el módulo.
patch_requests_once()
