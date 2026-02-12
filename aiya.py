import asyncio
import discord
import os
import sys
import io
import threading
import importlib.util
from collections import deque
from datetime import datetime
from discord import option
from discord.ext import commands
from core import ctxmenuhandler
from core import settings
from core.logging_setup import get_logger
from dotenv import load_dotenv
from core.queuehandler import GlobalQueue
from monitoring.power_monitor import PowerMonitor
from monitoring.windows_power_reader import (
    env_power_reader,
    env_sensor_debug_reader,
    env_sensor_snapshot_reader,
    env_temperature_reader,
)

#from core.mask_server import MaskEditorServer


# Load environment variables
load_dotenv()

# Setup intents and bot instance
intents = discord.Intents.default()
intents.message_content = True  # Make sure this is enabled for reading message content
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.logger = get_logger(__name__)

# Optional plotting backend for /power graph
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
    MATPLOTLIB_ERROR = None
except Exception as mpl_error:
    MATPLOTLIB_AVAILABLE = False
    MATPLOTLIB_ERROR = repr(mpl_error)
    mdates = None
    plt = None


def _python_env_debug() -> dict:
    site_paths = [p for p in sys.path if "site-packages" in p.lower()]
    return {
        "sys_executable": sys.executable,
        "sys_version": sys.version.replace("\n", " "),
        "cwd": os.getcwd(),
        "matplotlib_spec_found": bool(importlib.util.find_spec("matplotlib")),
        "matplotlib_available": MATPLOTLIB_AVAILABLE,
        "matplotlib_error": MATPLOTLIB_ERROR,
        "site_packages_preview": site_paths[:6],
        "sys_path_preview": sys.path[:8],
    }


bot.logger.info(f"[startup-python-env] {_python_env_debug()}")


POWER_WINDOWS_S = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "5h": 5 * 60 * 60,
    "24h": 24 * 60 * 60,
}

POWER_METRICS = {
    "total": ("total_w", "Total Power", "W"),
    "cpu_pkg": ("cpu_package_power_w", "CPU Package", "W"),
    "gpu_asic": ("gpu_asic_power_w", "GPU ASIC", "W"),
    "stapm": ("apu_stapm_w", "APU STAPM", "W"),
    "cpu_temp": ("cpu_temp_c", "CPU Temp", "C"),
    "gpu_temp": ("gpu_temp_c", "GPU Temp", "C"),
    "soc_temp": ("soc_temp_c", "SoC Temp", "C"),
}

HISTORY_MAX_SAMPLES = int(os.getenv("POWER_HISTORY_MAX_SAMPLES", "30000"))
power_history = deque(maxlen=HISTORY_MAX_SAMPLES)
power_history_lock = threading.Lock()

power_monitor = None
temperature_reader = None
sensor_snapshot_reader = None
sensor_debug_reader = None


def _window_seconds(window: str) -> int:
    return POWER_WINDOWS_S.get((window or "5h").lower(), POWER_WINDOWS_S["5h"])


def _window_label(window: str) -> str:
    w = (window or "5h").lower()
    return w if w in POWER_WINDOWS_S else "5h"


def _percentile(values, p):
    if not values:
        return None
    sorted_values = sorted(values)
    rank = int(round((len(sorted_values) - 1) * p))
    rank = max(0, min(rank, len(sorted_values) - 1))
    return sorted_values[rank]


def _history_records(window_s: int):
    cutoff = datetime.now().timestamp() - window_s
    with power_history_lock:
        return [row for row in power_history if row.get("ts", 0) >= cutoff]


def _metric_points(records, metric_key: str):
    points = []
    for row in records:
        value = row.get(metric_key)
        if value is None:
            continue
        points.append((row["ts"], float(value)))
    return points


def _energy_wh(points):
    if len(points) < 2:
        return 0.0
    total = 0.0
    prev_t, prev_v = points[0]
    for t, v in points[1:]:
        dt_s = t - prev_t
        if dt_s > 0:
            total += ((prev_v + v) / 2.0) * (dt_s / 3600.0)
        prev_t, prev_v = t, v
    return total


def _plot_metric(points, metric_label: str, unit: str, window_label_value: str):
    if not MATPLOTLIB_AVAILABLE:
        return None
    if not points:
        return None

    x = [datetime.fromtimestamp(ts) for ts, _ in points]
    y = [value for _, value in points]

    fig, ax = plt.subplots(figsize=(10, 4), dpi=130)
    ax.plot(x, y, linewidth=1.8, color="#f97316")
    ax.set_title(f"{metric_label} - last {window_label_value}")
    ax.set_ylabel(unit)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()

    image = io.BytesIO()
    fig.savefig(image, format="png")
    plt.close(fig)
    image.seek(0)
    return image


def _record_power_sample(ts: float, watts: float):
    sample = {
        "ts": float(ts),
        "total_w": float(watts),
    }

    snapshot_reader = sensor_snapshot_reader
    if snapshot_reader is not None:
        try:
            snapshot = snapshot_reader() or {}
        except Exception:
            snapshot = {}
        for key in (
            "cpu_package_power_w",
            "gpu_asic_power_w",
            "apu_stapm_w",
            "cpu_temp_c",
            "gpu_temp_c",
            "soc_temp_c",
        ):
            value = snapshot.get(key)
            if value is not None:
                sample[key] = float(value)

    with power_history_lock:
        power_history.append(sample)


enable_power_monitor = os.getenv("ENABLE_POWER_MONITOR", "False").lower() in ("true", "1", "t")
if enable_power_monitor:
    try:
        power_monitor = PowerMonitor(
            power_reader=env_power_reader(),
            sample_interval=float(os.getenv("POWER_SAMPLE_INTERVAL_S", "5")),
        )
        temperature_reader = env_temperature_reader()
        sensor_snapshot_reader = env_sensor_snapshot_reader()
        sensor_debug_reader = env_sensor_debug_reader()
        power_monitor.sample_callback = _record_power_sample
    except Exception as e:
        print(f"Warning: failed to initialize PowerMonitor: {e}")
        power_monitor = None
        temperature_reader = None
        sensor_snapshot_reader = None
        sensor_debug_reader = None

# Startup checks
try:
    settings.startup_check()
    settings.files_check()
    print("✅ Inicialización completada exitosamente")
except Exception as e:
    print(f"⚠️  Advertencia durante la inicialización: {e}")
    print("El bot continuará ejecutándose, pero algunas funciones pueden no estar disponibles.")

# Load extensions
bot.load_extension('core.settingscog')
bot.load_extension('core.stablecog')
bot.load_extension('core.upscalecog')
bot.load_extension('core.identifycog')
bot.load_extension('core.infocog')
bot.load_extension('core.leaderboardcog')

use_generate = os.getenv("USE_GENERATE", 'True')
enable_generate = use_generate.lower() in ('true', '1', 't')
if enable_generate:
    print(f"/generate command is ENABLED due to USE_GENERATE={use_generate}")
    bot.load_extension('core.generatecog')
else:
    print(f"/generate command is DISABLED due to USE_GENERATE={use_generate}")

use_chatbot = os.getenv("USE_CHATBOT", 'True')
enable_chatbot = use_chatbot.lower() in ('true', '1', 't')
if enable_chatbot:
    print(f"chatbot is ENABLED due to USE_CHATBOT={use_chatbot}")
    bot.load_extension('core.chatbotcog')
else:
    print(f"chatbot is DISABLED due to USE_CHATBOT={use_chatbot}")

# Stats slash command
@bot.slash_command(name='stats', description='How many images have I generated?')
async def stats(ctx):
    print(f"/Stats request -- {ctx.author.name}#{ctx.author.discriminator}")
    with open('resources/stats.txt', 'r') as f:
        data = list(map(int, f.readlines()))
    embed = discord.Embed(title='Art generated', description=f'I have created {data[0]} pictures!', color=discord.Color.random())
    await ctx.respond(embed=embed, delete_after=45.0)

# Queue slash command
@bot.slash_command(name='queue', description='Check the size of each queue')
async def queue(ctx):
    print(f"/Queue request -- {ctx.author.name}#{ctx.author.discriminator}")
    queue_sizes = GlobalQueue.get_queue_sizes()
    description = '\n'.join([f'{name}: {size}' for name, size in queue_sizes.items()])
    embed = discord.Embed(title='Queue Sizes', description=description, 
                          color=settings.global_var.embed_color)
    await ctx.respond(embed=embed, delete_after=45.0)

# Ping slash command
@bot.slash_command(name='ping', description='Pong!')
async def ping(ctx):
    print(f"/Ping request ({round(bot.latency * 1000)}ms)-- {ctx.author.name}#{ctx.author.discriminator}")
    # Check for an existing progression message, if yes delete the previous one
    async for old_msg in ctx.channel.history(limit=15):
        if old_msg.embeds:
            if old_msg.embeds[0].title.startswith("**Pong!** -"):
                await old_msg.delete()
    latency_ms = round(bot.latency * 1000)
    title = f'**Pong!** - `{latency_ms}ms`'
    embed = discord.Embed(title=title, color=discord.Color.random())
    await ctx.respond(content=f'<@{ctx.author.id}>', embed=embed, delete_after=10)


@bot.slash_command(name='power', description='Show live stats, summary, or graph')
@option(
    "view",
    str,
    description="Output mode",
    required=False,
    default="live",
    choices=["live", "summary", "graph"],
)
@option(
    "window",
    str,
    description="History window",
    required=False,
    default="5h",
    choices=["15m", "1h", "5h", "24h"],
)
@option(
    "metric",
    str,
    description="Metric for graph mode",
    required=False,
    default="total",
    choices=["total", "cpu_pkg", "gpu_asic", "stapm", "cpu_temp", "gpu_temp", "soc_temp"],
)
async def power(ctx, view: str = "live", window: str = "5h", metric: str = "total"):
    if power_monitor is None:
        await ctx.respond(
            "Power monitor is disabled. Set ENABLE_POWER_MONITOR=True in .env",
            delete_after=20,
        )
        return

    view = (view or "live").lower()
    window = _window_label(window)
    metric = (metric or "total").lower()
    if metric not in POWER_METRICS:
        metric = "total"

    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()
    sensor_source = ""
    if mode == "hwinfo_csv":
        sensor_source = os.getenv("HWINFO_CSV_PATH", "")
    elif mode in {"librehardwaremonitor", "lhm", "lhm_json"}:
        sensor_source = os.getenv("LHM_API_URL", "http://localhost:8085/data.json")
    bot.logger.info(
        f"[power] user={ctx.author} mode={mode} view={view} window={window} metric={metric} source={sensor_source} sample_interval={os.getenv('POWER_SAMPLE_INTERVAL_S', '5')}"
    )

    stats = power_monitor.get_stats()
    if stats["current_w"] is None:
        # Trigger an on-demand sample so /power can diagnose current state immediately.
        power_monitor.update()
        stats = power_monitor.get_stats()

    debug_info = {}
    if sensor_debug_reader is not None:
        try:
            debug_info = sensor_debug_reader() or {}
        except Exception as e:
            debug_info = {"debug_reader_error": str(e)}
    if debug_info:
        bot.logger.info(f"[power-debug] {debug_info}")

    if view == "live":
        if stats["current_w"] is None:
            description = (
                "No usable power reading yet.\n"
                "Check the bot logs for [power-debug] details.\n"
                "You can also test with POWER_READER=mock."
            )
        else:
            snapshot = {}
            if sensor_snapshot_reader is not None:
                try:
                    snapshot = sensor_snapshot_reader() or {}
                except Exception:
                    snapshot = {}

            temp_line = ""
            if temperature_reader is not None:
                try:
                    temp_c = temperature_reader()
                    if temp_c is not None:
                        temp_line = f"Temp: `{temp_c:.1f} C`\n"
                except Exception:
                    temp_line = ""

            extra_lines = ""
            if snapshot:
                if snapshot.get("cpu_temp_c") is not None:
                    extra_lines += f"CPU Temp: `{snapshot['cpu_temp_c']:.1f} C`\n"
                if snapshot.get("gpu_temp_c") is not None:
                    extra_lines += f"GPU Temp: `{snapshot['gpu_temp_c']:.1f} C`\n"
                if snapshot.get("soc_temp_c") is not None:
                    extra_lines += f"SoC Temp: `{snapshot['soc_temp_c']:.1f} C`\n"
                if snapshot.get("cpu_package_power_w") is not None:
                    extra_lines += f"CPU Pkg: `{snapshot['cpu_package_power_w']:.2f} W`\n"
                if snapshot.get("gpu_asic_power_w") is not None:
                    extra_lines += f"GPU ASIC: `{snapshot['gpu_asic_power_w']:.2f} W`\n"
                if snapshot.get("apu_stapm_w") is not None:
                    extra_lines += f"APU STAPM: `{snapshot['apu_stapm_w']:.2f} W`\n"

            description = (
                f"Current: `{stats['current_w']:.2f} W`\n"
                f"Min: `{stats['min_w']:.2f} W`\n"
                f"Max: `{stats['max_w']:.2f} W`\n"
                f"{temp_line}"
                f"{extra_lines}"
                f"Energy: `{stats['energy_Wh']:.3f} Wh` (`{stats['energy_kWh']:.6f} kWh`)\n"
                f"Elapsed: `{stats['elapsed_s']:.1f} s`"
            )

        embed = discord.Embed(
            title='Power Monitor',
            description=description,
            color=settings.global_var.embed_color,
        )
        await ctx.respond(embed=embed, delete_after=30)
        return

    window_s = _window_seconds(window)
    records = _history_records(window_s)
    if not records:
        await ctx.respond(
            f"No samples in the last {window}. Keep the bot running to build history.",
            delete_after=25,
        )
        return

    if view == "summary":
        lines = [
            f"Window: `{window}`",
            f"Samples: `{len(records)}`",
        ]

        for _, (metric_key, metric_label, metric_unit) in POWER_METRICS.items():
            points = _metric_points(records, metric_key)
            if not points:
                continue
            values = [v for _, v in points]
            p95 = _percentile(values, 0.95)
            lines.append(
                f"{metric_label}: avg `{(sum(values) / len(values)):.2f}` {metric_unit}, "
                f"min `{min(values):.2f}`, max `{max(values):.2f}`, p95 `{p95:.2f}`"
            )

        total_points = _metric_points(records, "total_w")
        total_wh = _energy_wh(total_points)
        lines.append(f"Energy ({window}): `{total_wh:.3f} Wh` (`{(total_wh / 1000.0):.6f} kWh`)")

        embed = discord.Embed(
            title="Power Summary",
            description="\n".join(lines),
            color=settings.global_var.embed_color,
        )
        await ctx.respond(embed=embed, delete_after=60)
        return

    metric_key, metric_label, metric_unit = POWER_METRICS[metric]
    points = _metric_points(records, metric_key)
    if not points:
        await ctx.respond(
            f"No data available for metric `{metric}` in the last {window}.",
            delete_after=25,
        )
        return

    plot_image = _plot_metric(points, metric_label, metric_unit, window)
    if plot_image is None:
        reason = MATPLOTLIB_ERROR if not MATPLOTLIB_AVAILABLE else "No plottable data."
        bot.logger.error(
            "[power-graph-unavailable] reason=%s env=%s",
            reason,
            _python_env_debug(),
        )
        await ctx.respond(
            f"Graph generation is unavailable. Reason: `{reason}`",
            delete_after=25,
        )
        return

    values = [v for _, v in points]
    summary_text = (
        f"Window: `{window}`\n"
        f"Samples: `{len(points)}`\n"
        f"Avg: `{(sum(values) / len(values)):.2f} {metric_unit}`\n"
        f"Min: `{min(values):.2f} {metric_unit}`\n"
        f"Max: `{max(values):.2f} {metric_unit}`"
    )
    embed = discord.Embed(
        title=f"Power Graph - {metric_label}",
        description=summary_text,
        color=settings.global_var.embed_color,
    )
    image_file = discord.File(fp=plot_image, filename="power_graph.png")
    embed.set_image(url="attachment://power_graph.png")
    await ctx.respond(embed=embed, file=image_file, delete_after=90)
    return

# Context menu commands
@bot.message_command(name="Get Image Info")
async def get_image_info(ctx, message: discord.Message):
    await ctxmenuhandler.get_image_info(ctx, message)

@bot.message_command(name=f"Quick Upscale")
async def quick_upscale(ctx, message: discord.Message):
    await ctxmenuhandler.quick_upscale(bot, ctx, message)

@bot.message_command(name=f"Download Batch")
async def batch_download(ctx, message: discord.Message):
    await ctxmenuhandler.batch_download(ctx, message)

# Event: on_ready
@bot.event
async def on_ready():
    bot.logger.info(f'Logged in as {bot.user.name} ({bot.user.id})')
    bot.logger.info(f"[startup-matplotlib] available={MATPLOTLIB_AVAILABLE} error={MATPLOTLIB_ERROR}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='drawing tutorials.'))
    await bot.sync_commands(force=True)
    if power_monitor is not None:
        power_monitor.start_background()
        bot.logger.info("PowerMonitor started in background mode")
    for guild in bot.guilds:
        print(f"I'm active in {guild.id} a.k.a {guild}!")

# Event: on_raw_reaction_add
@bot.event
async def on_raw_reaction_add(ctx):
    if ctx.emoji.name == '❌':
        try:
            end_user = f'{ctx.user_id}'
            message = await bot.get_channel(ctx.channel_id).fetch_message(ctx.message_id)
            if end_user in message.content and "Queue" not in message.content:
                await message.delete()
            # This is for deleting outputs from /identify
            if message.embeds:
                if message.embeds[0].footer.text == f'{ctx.member.name}#{ctx.member.discriminator}':
                    await message.delete()
        except(Exception,):
            # So console log isn't spammed with errors
            pass

# Event: on_guild_join
@bot.event
async def on_guild_join(guild):
    print(f'Wow, I joined {guild.name}!')

# Shutdown function
async def shutdown(bot):
    if power_monitor is not None:
        power_monitor.stop_background()
    await bot.close()

# Run the bot
try:
    bot.run(os.getenv('TOKEN'))
except KeyboardInterrupt:
    bot.logger.info('Keyboard interrupt received. Exiting.')
    asyncio.run(shutdown(bot))
except SystemExit:
    bot.logger.info('System exit received. Exiting.')
    asyncio.run(shutdown(bot))
except Exception as e:
    bot.logger.error(e)
    asyncio.run(shutdown(bot))
finally:
    sys.exit(0)
