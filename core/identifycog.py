import base64
import discord
import requests
from asyncio import AbstractEventLoop
from discord import option
from discord.ext import commands
from threading import Thread
from typing import Optional

from core import ctxmenuhandler
from core import queuehandler
from core import viewhandler
from core import settings
from core.logging_setup import get_logger
from core.queuehandler import GlobalQueue
from core.leaderboardcog import LeaderboardCog


class IdentifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger(__name__)

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(viewhandler.DeleteView(self))

    @commands.slash_command(name='identify', description='Describe an image')
    @option(
        'init_image',
        discord.Attachment,
        description='The image to identify.',
        required=False,
    )
    @option(
        'init_url',
        str,
        description='The URL image to identify. This overrides init_image!',
        required=False,
    )
    @option(
        'phrasing',
        str,
        description='The way the image will be described.',
        required=False,
        choices=['Normal', 'Tags', 'Image Info']
    )
    async def dream_handler(self, ctx: discord.ApplicationContext, *,
                            init_image: Optional[discord.Attachment] = None,
                            init_url: Optional[str] = None,
                            phrasing: Optional[str] = 'Normal'):
        image_url = init_url or (init_image.url if init_image else None)
        self.logger.info(
            "/Identify request -- %s#%s -- Image: %s, URL: %s, Phrasing: %s",
            ctx.author.name,
            ctx.author.discriminator,
            'provided' if init_image else 'None',
            init_url if init_url else 'None',
            phrasing,
        )

        if not image_url:
            await ctx.send_response('I need an image to identify!', ephemeral=True)
            self.logger.warning(
                "Identify rejected (no image): user=%s#%s",
                ctx.author.name,
                ctx.author.discriminator,
            )
            return

        # Update layman-friendly "phrasing" choices into what API understands
        if phrasing == 'Normal':
            phrasing = 'clip'
        elif phrasing == 'Tags':
            phrasing = 'deepdanbooru'
        else:
            await ctxmenuhandler.parse_image_info(ctx, image_url, "slash")
            return

        # set up tuple of parameters to pass into the Discord view
        input_tuple = (ctx, image_url, phrasing)
        view = viewhandler.DeleteView(input_tuple)
        # set up the queue if an image was found
        user_queue_limit = settings.queue_check(ctx.author)
        if queuehandler.GlobalQueue.dream_thread.is_alive():
            if user_queue_limit == "Stop":
                await ctx.send_response(content=f"Please wait! You're past your queue limit of {settings.global_var.queue_limit}.", ephemeral=True)
            else:
                queuehandler.GlobalQueue.queue.append(queuehandler.IdentifyObject(self, *input_tuple, view))
                self.logger.info(
                    "Identify enqueued: user_id=%s queue_size=%s",
                    ctx.author.id,
                    len(queuehandler.GlobalQueue.queue),
                )
        else:
            await queuehandler.process_dream(self, queuehandler.IdentifyObject(self, *input_tuple, view))
            self.logger.info("Identify started immediately: user_id=%s", ctx.author.id)

        if user_queue_limit != "Stop":
            await ctx.send_response(f"<@{ctx.author.id}>, I'm identifying the image!\nQueue: ``{len(queuehandler.GlobalQueue.queue)}``", delete_after=45.0)

    # the function to queue Discord posts
    def post(self, event_loop: AbstractEventLoop, post_queue_object: queuehandler.PostObject):
        event_loop.create_task(
            post_queue_object.ctx.channel.send(
                content=post_queue_object.content,
                embed=post_queue_object.embed,
                view=post_queue_object.view
            )
        )
        if queuehandler.GlobalQueue.post_queue:
            self.post(event_loop, queuehandler.GlobalQueue.post_queue.pop(0))

    def dream(self, event_loop: AbstractEventLoop, queue_object: queuehandler.IdentifyObject):
        should_update_leaderboard = False
        try:
            # construct a payload
            # Robust fetch of the image (Discord CDN/ephemeral links may require UA and can expire)
            try:
                img_resp = requests.get(
                    queue_object.init_image,
                    stream=True,
                    timeout=15,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'
                    }
                )
            except Exception as fetch_err:
                self.logger.warning("Identify image fetch failed: url=%s err=%s", queue_object.init_image, fetch_err)
                embed = discord.Embed(
                    title='identify failed',
                    description=f'Failed to download the image: {fetch_err}',
                    color=settings.global_var.embed_color
                )
                event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
                return

            if img_resp.status_code != 200:
                self.logger.warning(
                    "Identify image fetch bad status: status=%s url=%s",
                    img_resp.status_code,
                    queue_object.init_image,
                )
                embed = discord.Embed(
                    title='identify failed',
                    description=f'Failed to download the image (HTTP {img_resp.status_code}). The URL may have expired. Please resend the image.',
                    color=settings.global_var.embed_color
                )
                event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
                return

            content_type = img_resp.headers.get('Content-Type', '')
            content_len = int(img_resp.headers.get('Content-Length') or 0)
            self.logger.info(
                "Identify image fetched: status=%s content_type=%s bytes=%s",
                img_resp.status_code,
                content_type,
                content_len or 'unknown',
            )
            if 'image' not in content_type:
                embed = discord.Embed(
                    title='identify failed',
                    description='The provided link did not return a valid image. It may have expired or require authentication.',
                    color=settings.global_var.embed_color
                )
                event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
                return

            image = base64.b64encode(img_resp.content).decode('utf-8')
            mime = (content_type.split(';')[0] if content_type else 'image/png')
            payload = {
                "image": f'data:{mime};base64,' + image,
                "model": queue_object.phrasing
            }
            # send normal payload to webui
            s = settings.authenticate_user()

            response = s.post(url=f'{settings.global_var.url}/sdapi/v1/interrogate', json=payload)
            self.logger.info("Identify API response: status=%s", response.status_code)
            try:
                response_data = response.json()
            except Exception:
                body_preview = (response.text[:300] + '...') if response and response.text else 'empty'
                self.logger.warning("Identify API returned non-JSON response. Preview=%s", body_preview)
                response_data = {"error": f"Invalid API response (HTTP {response.status_code})"}

            # post to discord
            def post_dream():
                caption = (
                    response_data.get('caption')
                    or response_data.get('result')
                    or response_data.get('description')
                    or ''
                )
                if not caption:
                    # Friendly message when there is no description
                    keys = ','.join(list(response_data.keys())) if isinstance(response_data, dict) else 'n/a'
                    self.logger.warning("Identify API returned no caption. keys=%s", keys)
                    fail_msg = response_data.get('error') or 'The API did not return any description or tags.'
                    embed = discord.Embed(title='identify', description=fail_msg)
                    embed.set_image(url=queue_object.init_image)
                    embed.colour = settings.global_var.embed_color
                    footer_args = dict(text=f'{queue_object.ctx.author.name}#{queue_object.ctx.author.discriminator}')
                    if queue_object.ctx.author.avatar is not None:
                        footer_args['icon_url'] = queue_object.ctx.author.avatar.url
                    embed.set_footer(**footer_args)
                    queuehandler.process_post(
                        self, queuehandler.PostObject(
                            self, queue_object.ctx, content=f'<@{queue_object.ctx.author.id}>', file='', embed=embed, view=queue_object.view))
                    return
                embed_title = 'I think this is'
                if len(caption) > 4096:
                    caption = caption[:4096]

                embed = discord.Embed(title=f'{embed_title}', description=f'``{caption}``')
                embed.set_image(url=queue_object.init_image)
                embed.colour = settings.global_var.embed_color
                footer_args = dict(text=f'{queue_object.ctx.author.name}#{queue_object.ctx.author.discriminator}')
                if queue_object.ctx.author.avatar is not None:
                    footer_args['icon_url'] = queue_object.ctx.author.avatar.url
                embed.set_footer(**footer_args)

                queuehandler.process_post(
                    self, queuehandler.PostObject(
                        self, queue_object.ctx, content=f'<@{queue_object.ctx.author.id}>', file='', embed=embed, view=queue_object.view))
            Thread(target=post_dream, daemon=True).start()
            should_update_leaderboard = True

        except Exception as e:
            self.logger.exception("Unhandled exception while processing identify command")
            embed = discord.Embed(
                title='identify failed',
                description=f'Unexpected error while identifying the image: {e}',
                color=settings.global_var.embed_color,
            )
            event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
        finally:
            if should_update_leaderboard:
                LeaderboardCog.update_leaderboard(queue_object.ctx.author.id, str(queue_object.ctx.author), "Identify_Count")
            # Always continue with pending jobs, even when identify fails early.
            GlobalQueue.process_queue()


def setup(bot):
    bot.add_cog(IdentifyCog(bot))
