import os
from typing import Optional, Set

import discord
from discord import option
from discord.ext import commands

from core import queuehandler
from core import settings
from core.logging_setup import get_logger
from monitoring.stable_diffusion_manager import StableDiffusionProcessManager

logger = get_logger(__name__)


def _parse_allowed_user_ids() -> Set[int]:
    """
    Parse SD_CONTROL_ALLOWED_USER_IDS from env.

    Accepted formats:
    - "123,456"
    - "123;456"
    - "123 456"
    """
    raw = (os.getenv("SD_CONTROL_ALLOWED_USER_IDS") or "").strip()
    if not raw:
        return set()

    normalized = raw.replace(";", ",").replace(" ", ",")
    out: Set[int] = set()
    for token in normalized.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            logger.warning("Ignoring invalid user id in SD_CONTROL_ALLOWED_USER_IDS: %s", token)
    return out


def _safe_int_env(var_name: str, default: int) -> int:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s: %s. Using default=%s", var_name, raw, default)
        return default


class SDControlCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager = StableDiffusionProcessManager()

    def _is_authorized(self, ctx: discord.ApplicationContext) -> bool:
        allowed_ids = _parse_allowed_user_ids()
        if ctx.author.id in allowed_ids:
            return True

        guild = getattr(ctx, "guild", None)
        if guild is not None and ctx.author.guild_permissions.administrator:
            return True

        return False

    async def _interrupt_running_job(self) -> Optional[str]:
        """
        Try to interrupt any active SD generation before stop/restart.
        Returns an optional warning message.
        """
        try:
            session = settings.authenticate_user()
        except Exception as exc:
            return f"Could not create authenticated session for interrupt: {exc}"

        if session is None:
            return "Could not authenticate against the WebUI, skipped /interrupt call."

        try:
            session.post(url=f"{settings.global_var.url}/sdapi/v1/interrupt", timeout=8)
        except Exception as exc:
            return f"Failed to call /interrupt before process action: {exc}"
        return None

    @staticmethod
    def _cancel_pending_jobs() -> int:
        pending = len(queuehandler.GlobalQueue.queue)
        queuehandler.GlobalQueue.queue.clear()
        return pending

    @staticmethod
    def _format_status(status: dict) -> str:
        tracked_pid = status.get("tracked_pid")
        tracked_running = status.get("tracked_process_running")
        api_online = status.get("api_online")
        source = status.get("tracked_source")
        webui_url = status.get("webui_url")
        state_file = status.get("state_file")

        return (
            f"Action: `status`\n"
            f"WebUI URL: `{webui_url}`\n"
            f"API online: `{api_online}`\n"
            f"Tracked PID: `{tracked_pid}`\n"
            f"Tracked process running: `{tracked_running}`\n"
            f"Detected source: `{source}`\n"
            f"State file: `{state_file}`"
        )

    @commands.slash_command(
        name="sdcontrol",
        description="Manage the Stable Diffusion WebUI process for this host.",
    )
    @option(
        "action",
        str,
        description="Action to execute.",
        required=True,
        choices=["status", "start", "stop", "restart"],
    )
    @option(
        "cancel_pending",
        bool,
        description="Cancel queued AIYA jobs before stop/restart.",
        required=False,
        default=True,
    )
    @option(
        "wait_seconds",
        int,
        description="Delay before start during restart.",
        required=False,
        default=5,
        min_value=0,
        max_value=120,
    )
    async def sdcontrol_handler(
        self,
        ctx: discord.ApplicationContext,
        action: str,
        cancel_pending: bool = True,
        wait_seconds: int = 5,
    ):
        if not self._is_authorized(ctx):
            await ctx.respond(
                "You are not allowed to use `/sdcontrol`. "
                "You must be a server administrator or listed in SD_CONTROL_ALLOWED_USER_IDS.",
                ephemeral=True,
            )
            return

        await ctx.defer(ephemeral=True)

        action = (action or "").lower().strip()
        if action == "status":
            status = self.manager.get_status()
            await ctx.followup.send(self._format_status(status), ephemeral=True)
            return

        notes = []
        if action in {"stop", "restart"}:
            interrupt_warning = await self._interrupt_running_job()
            if interrupt_warning:
                notes.append(f"- {interrupt_warning}")
            else:
                notes.append("- Sent `/interrupt` to WebUI.")

            if cancel_pending:
                removed = self._cancel_pending_jobs()
                notes.append(f"- Cleared `{removed}` pending queue item(s).")

        if action == "start":
            timeout_s = _safe_int_env("SD_START_TIMEOUT_S", 120)
            result = self.manager.start(wait_for_api=True, timeout_s=timeout_s)
        elif action == "stop":
            result = self.manager.stop()
        else:
            timeout_s = _safe_int_env("SD_START_TIMEOUT_S", 120)
            result = self.manager.restart(wait_before_start_s=wait_seconds, timeout_s=timeout_s)

        lines = [
            f"Action: `{action}`",
            f"Success: `{result.ok}`",
            f"Message: {result.message}",
            f"Tracked PID: `{result.pid}`",
            f"API online: `{result.api_online}`",
        ]
        if notes:
            lines.append("Preparation:")
            lines.extend(notes)

        await ctx.followup.send("\n".join(lines), ephemeral=True)


def setup(bot):
    bot.add_cog(SDControlCog(bot))
