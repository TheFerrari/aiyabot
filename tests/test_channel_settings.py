import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core import settings, model_profiles
from core.settingscog import SettingsCog
from test_model_profiles import CONFIG, fixture
from test_profile_integration import DEFAULTS


class ChannelSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for channel in ('1', '2'):
            (self.root / (channel + '.json')).write_text(json.dumps(dict(DEFAULTS, data_model='XL')))
        self.enterContext(patch.object(settings, 'path', str(self.root) + '/'))
        self.enterContext(patch.object(settings, 'template', {}))
        self.enterContext(patch.object(settings.global_var, 'model_info', {
            'XL': ('xl.safetensors [aaaa]', 'xl', 'aaaa', ''),
            'Anima': ('anima.safetensors [bbbb]', 'anima', 'bbbb', ''),
        }))
        self.enterContext(patch.object(model_profiles, 'load_config', return_value=CONFIG))

    async def command(self, **kwargs):
        ctx = SimpleNamespace(channel=SimpleNamespace(id=1), send_response=AsyncMock())
        await SettingsCog.settings_handler.callback(SettingsCog(Mock()), ctx, **kwargs)
        ctx.send_response.assert_awaited_once()
        return ctx

    def resolve(self, architecture):
        return model_profiles.resolve(fixture(), CONFIG, architecture, {}, settings.read('1'))

    async def test_save_anima_overrides_without_changing_xl_or_other_channel(self):
        other = settings.read('2')
        await self.command(parameter_profile='anima', steps=40, sampler='Euler a',
                           scheduler='Automatic', guidance_scale='6', distilled_cfg_scale='2,5')
        values = self.resolve('anima')['parameters']
        self.assertEqual(values['steps'], 40)
        self.assertEqual(values['sampler'], 'Euler a')
        self.assertEqual(values['scheduler'], 'Automatic')
        self.assertEqual(values['guidance_scale'], 6)
        self.assertEqual(values['distilled_cfg_scale'], 2.5)
        self.assertEqual(self.resolve('xl')['parameters']['steps'], DEFAULTS['steps'])
        self.assertEqual(settings.read('2'), other)

    async def test_existing_settings_command_uses_default_model_architecture(self):
        await self.command(steps=14, sampler='Euler a')
        self.assertEqual(self.resolve('xl')['parameters']['steps'], 14)
        self.assertEqual(self.resolve('anima')['parameters']['steps'], 32)
        self.assertEqual(settings.read('1')['steps'], 14)
        await self.command(data_model='Anima', steps=35)
        self.assertEqual(self.resolve('anima')['parameters']['steps'], 35)
        self.assertEqual(self.resolve('xl')['parameters']['steps'], 14)

    async def test_reset_then_custom_value(self):
        await self.command(parameter_profile='xl', steps=14)
        await self.command(parameter_profile='xl', reset_profile=True)
        self.assertEqual(self.resolve('xl')['parameters']['steps'], 24)
        self.assertEqual(self.resolve('xl')['parameters']['sampler'], 'Euler a')
        await self.command(parameter_profile='xl', steps=9)
        self.assertEqual(self.resolve('xl')['parameters']['steps'], 9)
        self.assertEqual(self.resolve('xl')['parameters']['sampler'], 'Euler a')

    async def test_invalid_scale_does_not_modify_channel(self):
        before = settings.read('1')
        await self.command(parameter_profile='anima', distilled_cfg_scale='nan', steps=10)
        self.assertEqual(settings.read('1'), before)

    async def test_discord_option_limit(self):
        import discord
        bot = discord.Bot()
        bot.add_cog(SettingsCog(bot))
        command = next(c for c in bot.pending_application_commands if c.name == 'settings')
        self.assertEqual(len(command.options), 25)
        self.assertNotIn('ctx', [o.name for o in command.options])
        await bot.close()


class PrefixTests(unittest.TestCase):
    def test_explicit_prefix_does_not_depend_on_mutable_global_prefix(self):
        prefix = '<lora:Turbo-ANIMA-v4:0.8>'
        with patch.object(settings.global_var, 'prompt_prefix', ''), \
             patch.object(settings.global_var, 'prompt_ignore_list', []), \
             patch.object(settings.global_var, 'negative_prompt_prefix', []):
            result = settings.prompt_mod('a garden', '', prompt_prefix_override=prefix)
        self.assertEqual(result[0], 'Mod')
        self.assertEqual(result[1], prefix + ', a garden')

    def test_prefix_is_not_duplicated_and_moderation_still_applies(self):
        prefix = '<lora:Turbo-ANIMA-v4:0.8>'
        prompt = prefix.lower() + ', a garden'
        with patch.object(settings.global_var, 'prompt_ban_list', []):
            result = settings.prompt_mod(prompt, '', prompt_prefix_override=prefix)
        self.assertEqual(result[1], prompt)
        with patch.object(settings.global_var, 'prompt_ban_list', ['bannedword']):
            result = settings.prompt_mod('bannedword', '', prompt_prefix_override=prefix)
        self.assertEqual(result[0], 'Stop')
