import json
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from core.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class SDProcessResult:
    ok: bool
    action: str
    message: str
    pid: Optional[int] = None
    api_online: Optional[bool] = None


@dataclass
class SDLaunchSpec:
    command: str | list[str]
    workdir: Optional[str]
    shell: bool
    display_command: str


class StableDiffusionProcessManager:
    """
    Process manager for a local Stable Diffusion WebUI instance.

    Behavior:
    - Tracks only processes started through this manager.
    - Prevents duplicate starts by checking both tracked PID and API availability.
    - Stores process metadata in a local state file to survive bot restarts.
    """

    def __init__(self, state_file: Optional[str] = None):
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        runtime_dir = os.path.join(os.path.dirname(__file__), "runtime")
        self._state_file = state_file or os.path.join(runtime_dir, "stable_diffusion_process.json")

    def get_status(self) -> dict:
        with self._lock:
            tracked_pid = self._resolve_tracked_pid()
            api_online = self._is_api_online()
            status = {
                "tracked_pid": tracked_pid,
                "tracked_process_running": bool(tracked_pid),
                "api_online": api_online,
                "webui_url": self._webui_url(),
                "state_file": self._state_file,
            }

            if tracked_pid is not None:
                status["tracked_source"] = "bot-managed"
            elif api_online:
                status["tracked_source"] = "external-or-untracked"
            else:
                status["tracked_source"] = "none"

            return status

    def start(self, wait_for_api: bool = True, timeout_s: int = 90) -> SDProcessResult:
        with self._lock:
            tracked_pid = self._resolve_tracked_pid()
            if tracked_pid is not None:
                return SDProcessResult(
                    ok=False,
                    action="start",
                    message="Stable Diffusion is already running (tracked by bot).",
                    pid=tracked_pid,
                    api_online=self._is_api_online(),
                )

            if self._is_api_online():
                return SDProcessResult(
                    ok=False,
                    action="start",
                    message="Stable Diffusion API is already online (untracked process). Start aborted to avoid duplicates.",
                    pid=None,
                    api_online=True,
                )

            try:
                launch_spec = self._build_launch_spec()
            except ValueError as exc:
                return SDProcessResult(
                    ok=False,
                    action="start",
                    message=str(exc),
                    pid=None,
                    api_online=False,
                )

            try:
                creationflags = 0
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

                proc = subprocess.Popen(
                    launch_spec.command,
                    cwd=launch_spec.workdir,
                    shell=launch_spec.shell,
                    creationflags=creationflags,
                )
            except Exception as exc:
                logger.exception("Unable to start Stable Diffusion process.")
                return SDProcessResult(
                    ok=False,
                    action="start",
                    message=f"Failed to start Stable Diffusion: {exc}",
                    pid=None,
                    api_online=False,
                )

            self._process = proc
            self._write_state(
                pid=proc.pid,
                display_command=launch_spec.display_command,
                workdir=launch_spec.workdir,
            )
            logger.info("Stable Diffusion start requested. pid=%s command=%s", proc.pid, launch_spec.display_command)

        if not wait_for_api:
            return SDProcessResult(
                ok=True,
                action="start",
                message=f"Start signal sent. PID={proc.pid}.",
                pid=proc.pid,
                api_online=self._is_api_online(),
            )

        ready = self.wait_until_api_online(timeout_s=timeout_s)
        if ready:
            return SDProcessResult(
                ok=True,
                action="start",
                message=f"Stable Diffusion is online. PID={proc.pid}.",
                pid=proc.pid,
                api_online=True,
            )

        return SDProcessResult(
            ok=True,
            action="start",
            message=(
                f"Stable Diffusion launch command started (PID={proc.pid}), "
                f"but API was not reachable within {timeout_s}s."
            ),
            pid=proc.pid,
            api_online=False,
        )

    def stop(self) -> SDProcessResult:
        with self._lock:
            tracked_pid = self._resolve_tracked_pid()
            api_online = self._is_api_online()

            if tracked_pid is None:
                if api_online:
                    return SDProcessResult(
                        ok=False,
                        action="stop",
                        message=(
                            "Stable Diffusion API is online, but the process is not tracked by the bot. "
                            "Refusing to stop untracked process."
                        ),
                        pid=None,
                        api_online=True,
                    )
                return SDProcessResult(
                    ok=False,
                    action="stop",
                    message="No tracked Stable Diffusion process is running.",
                    pid=None,
                    api_online=False,
                )

            stop_ok, stop_message = self._terminate_pid_tree(tracked_pid)
            if stop_ok:
                self._process = None
                self._clear_state()
            elif not self._is_pid_running(tracked_pid):
                self._process = None
                self._clear_state()

        api_online_after = self._is_api_online()
        if stop_ok:
            return SDProcessResult(
                ok=True,
                action="stop",
                message=stop_message,
                pid=tracked_pid,
                api_online=api_online_after,
            )

        return SDProcessResult(
            ok=False,
            action="stop",
            message=stop_message,
            pid=tracked_pid,
            api_online=api_online_after,
        )

    def restart(self, wait_before_start_s: int = 5, timeout_s: int = 90) -> SDProcessResult:
        with self._lock:
            status = self.get_status()
            tracked_running = bool(status["tracked_process_running"])
            api_online = bool(status["api_online"])

        if tracked_running:
            stop_result = self.stop()
            if not stop_result.ok:
                return SDProcessResult(
                    ok=False,
                    action="restart",
                    message=f"Failed to stop tracked process before restart: {stop_result.message}",
                    pid=stop_result.pid,
                    api_online=stop_result.api_online,
                )
        elif api_online:
            return SDProcessResult(
                ok=False,
                action="restart",
                message=(
                    "Stable Diffusion is online but untracked by the bot. "
                    "Cannot restart safely because stop would target an unknown process."
                ),
                pid=None,
                api_online=True,
            )

        wait_before_start_s = max(0, int(wait_before_start_s))
        if wait_before_start_s:
            time.sleep(wait_before_start_s)

        start_result = self.start(wait_for_api=True, timeout_s=timeout_s)
        return SDProcessResult(
            ok=start_result.ok,
            action="restart",
            message=f"Restart result: {start_result.message}",
            pid=start_result.pid,
            api_online=start_result.api_online,
        )

    def wait_until_api_online(self, timeout_s: int = 90, interval_s: float = 1.5) -> bool:
        timeout_s = max(1, int(timeout_s))
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._is_api_online(timeout=3):
                return True
            time.sleep(interval_s)
        return self._is_api_online(timeout=3)

    def _build_launch_spec(self) -> SDLaunchSpec:
        start_command = self._clean_env("SD_START_COMMAND")
        start_workdir = self._clean_env("SD_START_WORKDIR")
        start_bat_path = self._clean_env("SD_START_BAT_PATH")
        folder = self._clean_env("SD_FOLDER_PATH")
        bat_name = self._clean_env("SD_START_BAT_FILE_NAME")

        if start_command:
            workdir = start_workdir
            if workdir and not os.path.isdir(workdir):
                raise ValueError(f"SD_START_WORKDIR does not exist: {workdir}")
            parsed_command = shlex.split(start_command, posix=False)
            if not parsed_command:
                raise ValueError("SD_START_COMMAND is empty after parsing.")

            command: str | list[str]
            shell = False
            first_token = parsed_command[0].lower()
            if os.name == "nt" and (first_token.endswith(".bat") or first_token.endswith(".cmd")):
                command = ["cmd", "/c", *parsed_command]
            else:
                command = parsed_command
            return SDLaunchSpec(
                command=command,
                workdir=workdir,
                shell=shell,
                display_command=start_command,
            )

        resolved_bat_path: Optional[str] = None
        if start_bat_path:
            resolved_bat_path = os.path.abspath(start_bat_path)
        elif folder and bat_name:
            resolved_bat_path = os.path.abspath(os.path.join(folder, bat_name))

        if not resolved_bat_path:
            raise ValueError(
                "No SD start command configured. Set SD_START_COMMAND or SD_START_BAT_PATH, "
                "or use SD_FOLDER_PATH + SD_START_BAT_FILE_NAME."
            )

        if not os.path.isfile(resolved_bat_path):
            raise ValueError(f"Stable Diffusion start file not found: {resolved_bat_path}")

        workdir = start_workdir or os.path.dirname(resolved_bat_path)
        if not os.path.isdir(workdir):
            raise ValueError(f"Stable Diffusion working directory does not exist: {workdir}")

        if os.name == "nt":
            command: str | list[str] = ["cmd", "/c", resolved_bat_path]
            shell = False
        else:
            command = [resolved_bat_path]
            shell = False

        return SDLaunchSpec(
            command=command,
            workdir=workdir,
            shell=shell,
            display_command=resolved_bat_path,
        )

    def _terminate_pid_tree(self, pid: int) -> tuple[bool, str]:
        if not self._is_pid_running(pid):
            return True, f"Tracked process PID={pid} was already stopped."

        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0:
                return True, f"Stable Diffusion process tree stopped (PID={pid})."
            if not self._is_pid_running(pid):
                return True, f"Stable Diffusion process PID={pid} is no longer running."
            stderr = (completed.stderr or "").strip()
            return False, f"taskkill failed for PID={pid}. {stderr}"

        try:
            os.kill(pid, 15)
        except Exception as exc:
            if not self._is_pid_running(pid):
                return True, f"Stable Diffusion process PID={pid} is no longer running."
            return False, f"Failed to stop PID={pid}: {exc}"

        return True, f"Stable Diffusion process stop signal sent to PID={pid}."

    def _resolve_tracked_pid(self) -> Optional[int]:
        if self._process is not None:
            if self._process.poll() is None:
                return self._process.pid
            self._process = None

        state = self._read_state()
        pid = state.get("pid")
        if not isinstance(pid, int):
            self._clear_state()
            return None
        if not self._is_pid_running(pid):
            self._clear_state()
            return None
        return pid

    def _is_api_online(self, timeout: int = 3) -> bool:
        url = f"{self._webui_url()}/sdapi/v1/cmd-flags"
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code in (200, 401, 404):
                return True
            return response.status_code < 500
        except Exception:
            return False

    def _webui_url(self) -> str:
        try:
            from core import settings  # local import to avoid startup side effects

            configured = (getattr(settings.global_var, "url", "") or "").strip()
            if configured:
                return configured.rstrip("/")
        except Exception:
            pass

        from_env = (os.getenv("URL") or "").strip()
        if from_env:
            return from_env.rstrip("/")
        return "http://127.0.0.1:7860"

    def _read_state(self) -> dict:
        if not os.path.isfile(self._state_file):
            return {}
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_state(self, pid: int, display_command: str, workdir: Optional[str]) -> None:
        state_dir = os.path.dirname(self._state_file)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        payload = {
            "pid": pid,
            "command": display_command,
            "workdir": workdir,
            "started_at": int(time.time()),
        }
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _clear_state(self) -> None:
        try:
            if os.path.isfile(self._state_file):
                os.remove(self._state_file)
        except Exception:
            pass

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    @staticmethod
    def _clean_env(var_name: str) -> Optional[str]:
        value = os.getenv(var_name)
        if value is None:
            return None
        cleaned = value.strip().strip('"').strip("'")
        return cleaned or None
