import base64
import discord
import traceback
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
from core.queuehandler import GlobalQueue
from core.leaderboardcog import LeaderboardCog


class IdentifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
                            init_url: Optional[str],
                            phrasing: Optional[str] = 'Normal'):
        print(f"/Identify request -- {ctx.author.name}#{ctx.author.discriminator} -- Image: {init_image if init_image else 'None'}, URL: {init_url if init_url else 'None'}")

        has_image = True
        # url *will* override init image for compatibility, can be changed here
        if init_url:
            try:
                init_image = requests.get(init_url)
            except(Exception,):
                await ctx.send_response('URL image not found!\nI have nothing to work with...', ephemeral=True)
                has_image = False

        # fail if no image is provided
        if init_url is None:
            if init_image is None:
                await ctx.send_response('I need an image to identify!', ephemeral=True)
                has_image = False

        # Update layman-friendly "phrasing" choices into what API understands
        if phrasing == 'Normal':
            phrasing = 'clip'
        elif phrasing == 'Tags':
            phrasing = 'deepdanbooru'
        else:
            await ctxmenuhandler.parse_image_info(ctx, init_image.url, "slash")
            return

        # set up tuple of parameters to pass into the Discord view
        input_tuple = (ctx, init_image.url, phrasing)
        view = viewhandler.DeleteView(input_tuple)
        # set up the queue if an image was found
        user_queue_limit = settings.queue_check(ctx.author)
        if has_image:
            if queuehandler.GlobalQueue.dream_thread.is_alive():
                if user_queue_limit == "Stop":
                    await ctx.send_response(content=f"Please wait! You're past your queue limit of {settings.global_var.queue_limit}.", ephemeral=True)
                else:
                    queuehandler.GlobalQueue.queue.append(queuehandler.IdentifyObject(self, *input_tuple, view))
            else:
                await queuehandler.process_dream(self, queuehandler.IdentifyObject(self, *input_tuple, view))
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
            self.post(self.event_loop, self.queue.pop(0))

    def dream(self, event_loop: AbstractEventLoop, queue_object: queuehandler.IdentifyObject):
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
                print(f"[identify] Image fetch failed: url={queue_object.init_image} err={fetch_err}")
                embed = discord.Embed(
                    title='identify failed',
                    description=f'Failed to download the image: {fetch_err}',
                    color=settings.global_var.embed_color
                )
                event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
                return

            if img_resp.status_code != 200:
                print(f"[identify] Image fetch bad status: {img_resp.status_code} url={queue_object.init_image}")
                embed = discord.Embed(
                    title='identify failed',
                    description=f'Failed to download the image (HTTP {img_resp.status_code}). The URL may have expired. Please resend the image.',
                    color=settings.global_var.embed_color
                )
                event_loop.create_task(queue_object.ctx.channel.send(embed=embed))
                return

            content_type = img_resp.headers.get('Content-Type', '')
            content_len = int(img_resp.headers.get('Content-Length') or 0)
            print(f"[identify] Image fetched: status={img_resp.status_code} content_type={content_type} bytes={content_len or 'unknown'}")
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
            print(f"[identify] API response: status={response.status_code}")
            try:
                response_data = response.json()
            except Exception:
                body_preview = (response.text[:300] + '...') if response and response.text else 'empty'
                print(f"[identify] Failed to parse JSON. Body preview: {body_preview}")
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
                    print(f"[identify] No caption returned. Response keys: {keys}")
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

        except Exception as e:
            embed = discord.Embed(title='identify failed', description=f'{e}\n{traceback.print_exc()}',
                                  color=settings.global_var.embed_color)
            event_loop.create_task(queue_object.ctx.channel.send(embed=embed))

        # update the leaderboard
        LeaderboardCog.update_leaderboard(queue_object.ctx.author.id, str(queue_object.ctx.author), "Identify_Count")

        # check each queue for any remaining tasks
        GlobalQueue.process_queue()


def setup(bot):
    bot.add_cog(IdentifyCog(bot))
