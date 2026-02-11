import asyncio
import discord
import os
import sys
from discord.ext import commands
from core import ctxmenuhandler
from core import settings
from core.logging_setup import get_logger
from dotenv import load_dotenv
from core.queuehandler import GlobalQueue
from monitoring.power_monitor import PowerMonitor
from monitoring.windows_power_reader import (
    env_hwinfo_debug_reader,
    env_hwinfo_snapshot_reader,
    env_power_reader,
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

power_monitor = None
temperature_reader = None
hwinfo_snapshot_reader = None
hwinfo_debug_reader = None
enable_power_monitor = os.getenv("ENABLE_POWER_MONITOR", "False").lower() in ("true", "1", "t")
if enable_power_monitor:
    try:
        power_monitor = PowerMonitor(
            power_reader=env_power_reader(),
            sample_interval=float(os.getenv("POWER_SAMPLE_INTERVAL_S", "5")),
        )
        temperature_reader = env_temperature_reader()
        hwinfo_snapshot_reader = env_hwinfo_snapshot_reader()
        hwinfo_debug_reader = env_hwinfo_debug_reader()
    except Exception as e:
        print(f"Warning: failed to initialize PowerMonitor: {e}")
        power_monitor = None
        temperature_reader = None
        hwinfo_snapshot_reader = None
        hwinfo_debug_reader = None

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


@bot.slash_command(name='power', description='Show host power and thermal metrics')
async def power(ctx):
    if power_monitor is None:
        await ctx.respond(
            "Power monitor is disabled. Set ENABLE_POWER_MONITOR=True in .env",
            delete_after=20,
        )
        return

    mode = os.getenv("POWER_READER", "hwinfo_csv")
    csv_path = os.getenv("HWINFO_CSV_PATH", "")
    bot.logger.info(
        f"[power] user={ctx.author} mode={mode} csv_path={csv_path} sample_interval={os.getenv('POWER_SAMPLE_INTERVAL_S', '5')}"
    )

    stats = power_monitor.get_stats()

    debug_info = {}
    if hwinfo_debug_reader is not None:
        try:
            debug_info = hwinfo_debug_reader() or {}
        except Exception as e:
            debug_info = {"debug_reader_error": str(e)}
    if debug_info:
        bot.logger.info(f"[power-debug] {debug_info}")

    if stats["current_w"] is None:
        description = (
            "No usable power reading yet.\n"
            "Check the bot logs for [power-debug] details.\n"
            "You can also test with POWER_READER=mock."
        )
    else:
        snapshot = {}
        if hwinfo_snapshot_reader is not None:
            try:
                snapshot = hwinfo_snapshot_reader() or {}
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
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='drawing tutorials.'))
    await bot.sync_commands()
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
