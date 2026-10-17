"""Exercise the actual Discord command and queue payload with local mocks only."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core import settings, queuehandler, model_profiles
from core import stablecog, viewhandler
from test_model_profiles import fixture, CONFIG, CHANNEL


DEFAULTS = dict(CHANNEL, negative_prompt='', data_model='Anima', style='None',
                highres_fix='Disabled', clip_skip=2, strength='0.5', batch='1,1',
                max_batch='4,1', hypernet='None', hyper_multi='0.85',
                lora='xl-only', lora_multi='0.85', upscaler_1='Latent', live_preview=False)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def make_job(self, **kwargs):
        ctx = SimpleNamespace(author=SimpleNamespace(id=1, name='tester'),
                              channel=SimpleNamespace(id=2, send=AsyncMock()),
                              interaction=SimpleNamespace(response=SimpleNamespace(is_done=lambda: True)),
                              followup=SimpleNamespace(send=AsyncMock()))
        captured = []
        async def enqueue(cog, job):
            captured.append(job)
        def resolve(checkpoint, explicit, channel, mode):
            return model_profiles.resolve(fixture(), CONFIG, checkpoint, explicit, channel, mode)
        with patch.object(settings, 'read', return_value=deepcopy(DEFAULTS)), \
             patch.object(settings, 'check'), patch.object(settings, 'queue_check'), \
             patch.object(settings, 'messages', return_value='wait'), \
             patch.object(settings, 'check_webui_running', return_value=False), \
             patch.object(settings.global_var, 'model_info', {'Anima': ('anima.safetensors [bbbb]', 'anima', 'bbbb', '')}), \
             patch.object(settings.global_var, 'prompt_prefix', '<lora:xl-only:1>'), \
             patch.object(stablecog, 'resolve_model_profile', side_effect=resolve), \
             patch.object(stablecog, '_wake_sd_host_proactively'), \
             patch.object(queuehandler.GlobalQueue, 'dream_thread', SimpleNamespace(is_alive=lambda: False)), \
             patch.object(queuehandler, 'process_dream', side_effect=enqueue):
            cog = stablecog.StableCog(Mock())
            await stablecog.StableCog.dream_handler.callback(cog, ctx, prompt='a garden', **kwargs)
        self.assertEqual(len(captured), 1, ctx.followup.send.call_args_list)
        return captured[0]

    async def test_command_resolves_before_channel_defaults_and_freezes_profile(self):
        job = await self.make_job()
        self.assertEqual(job.steps, 32)
        self.assertEqual(job.sampler, 'ER SDE')
        self.assertEqual(job.prompt, 'a garden')
        self.assertEqual(job.clip_skip, 1)
        self.assertEqual(job.model_profile, job.view.input_tuple[22])
        job.model_profile['override_settings']['forge_additional_modules'].clear()
        self.assertEqual(len(job.view.input_tuple[22]['override_settings']['forge_additional_modules']), 2)

    async def test_explicit_command_values_and_replay_serialization(self):
        job = await self.make_job(steps=14, guidance_scale='5,5', distilled_cfg_scale='2.5', scheduler='automatic')
        self.assertEqual(job.steps, 14)
        self.assertEqual(job.guidance_scale, 5.5)
        self.assertEqual(job.distilled_cfg_scale, 2.5)
        encoded = viewhandler.serialize_input_tuple(job.view.input_tuple)
        decoded = viewhandler.deserialize_input_tuple(encoded)
        self.assertEqual(decoded[22], job.model_profile)
        self.assertEqual(decoded[5:22], job.view.input_tuple[5:22])
        del encoded['model_profile']
        self.assertIsNone(viewhandler.deserialize_input_tuple(encoded)[22])
        legacy = queuehandler.DrawObject(job.cog, *job.view.input_tuple[:22], job.view)
        self.assertIsNone(legacy.model_profile)
        self.assertIs(legacy.view, job.view)

    async def test_modal_checkpoint_switch_applies_new_architecture_defaults(self):
        job = await self.make_job()
        mapping = {'Anima': ('anima.safetensors [bbbb]', 'anima', 'bbbb', ''),
                   'XL': ('xl.safetensors [aaaa]', 'xl', 'aaaa', '')}
        interaction = SimpleNamespace(user=SimpleNamespace(id=1, name='tester', discriminator='0'),
                                      response=SimpleNamespace(send_message=AsyncMock()))
        def resolve(checkpoint, explicit, channel, mode):
            return model_profiles.resolve(fixture(), CONFIG, checkpoint, explicit, channel, mode)
        with patch.object(settings, 'read', return_value=DEFAULTS), \
             patch.object(settings, 'check'), patch.object(settings, 'queue_check'), \
             patch.object(settings, 'messages', return_value='wait'), \
             patch.object(settings.global_var, 'model_info', mapping), \
             patch.object(settings.global_var, 'style_names', {'None': ''}), \
             patch.object(settings.global_var, 'extra_nets', ['None']), \
             patch.object(settings.global_var, 'sampler_names', ['ER SDE', 'Euler a']), \
             patch.object(settings.global_var, 'scheduler_names', ['Beta', 'Automatic']), \
             patch.object(stablecog, 'resolve_model_profile', side_effect=resolve), \
             patch.object(queuehandler.GlobalQueue, 'dream_thread', SimpleNamespace(is_alive=lambda: False)), \
             patch.object(queuehandler, 'process_dream', new_callable=AsyncMock) as enqueue:
            modal = viewhandler.DrawModal(job.view.input_tuple)
            modal.children[3].value = modal.children[3].value.replace('data_model:Anima', 'data_model:XL')
            await modal.callback(interaction)
            enqueue.assert_awaited_once()
            changed = enqueue.call_args.args[1]
            self.assertEqual(changed.model_profile['architecture'], 'xl')
            self.assertEqual(changed.steps, DEFAULTS["steps"])
            self.assertEqual(changed.sampler, DEFAULTS['sampler'])
            self.assertEqual(changed.model_profile['override_settings']['forge_additional_modules'], [])

    async def test_meta_button_preserves_anima_sampling_fields(self):
        from core.metacog import MetaView
        ctx = SimpleNamespace()
        cog = Mock()
        interaction = SimpleNamespace(
            client=SimpleNamespace(get_cog=Mock(return_value=cog)),
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        view = MetaView(ctx, '', 'a garden', '', '32', 'ER SDE', '4', '123',
                        '1024x1024', 'N/A', 'Anima', 'Beta', '3')
        with patch.object(stablecog.StableCog.dream_handler, '_callback', new_callable=AsyncMock) as draw:
            await MetaView.draw_from_meta(view, Mock(), interaction)
        draw.assert_awaited_once()
        self.assertEqual(draw.call_args.args, (cog, ctx))
        self.assertEqual(draw.call_args.kwargs['data_model'], 'Anima')
        self.assertEqual(draw.call_args.kwargs['scheduler'], 'Beta')
        self.assertEqual(draw.call_args.kwargs['distilled_cfg_scale'], '3')

    async def test_actual_generation_payload_contains_modules_and_hires_profile(self):
        job = await self.make_job(width=512, height=512, highres_fix='Latent', adetailer='Faces')
        session = Mock()
        session.post.return_value.json.return_value = {'images': []}
        loop = Mock()
        loop.create_task.side_effect = lambda coroutine: coroutine.close()
        with patch.object(settings, 'read', return_value=DEFAULTS), \
             patch.object(settings, 'authenticate_user', return_value=session), \
             patch.object(settings, 'fuzzy_get_id_name', return_value=(1, 'tester')), \
             patch.object(stablecog, '_wake_sd_host_proactively'), \
             patch.object(queuehandler.GlobalQueue, 'update_progress_message', new=Mock(return_value=None)), \
             patch.object(stablecog, 'run_coroutine_threadsafe'), \
             patch.object(queuehandler.GlobalQueue, 'process_queue'):
            job.cog.dream(loop, job)
        self.assertEqual(session.post.call_count, 1)
        call = session.post.call_args.kwargs
        self.assertTrue(call['url'].endswith('/sdapi/v1/txt2img'))
        payload = call['json']
        self.assertEqual(len(payload['override_settings']['forge_additional_modules']), 2)
        self.assertNotIn('CLIP_stop_at_last_layers', payload['override_settings'])
        self.assertTrue(payload['override_settings_restore_afterwards'])
        self.assertEqual(payload['hr_distilled_cfg'], 2)
        self.assertEqual(payload['hr_second_pass_steps'], 16)
        self.assertEqual(payload['hr_resize_x'] % 64, 0)
        self.assertIn('ADetailer', payload['alwayson_scripts'])


class BackendTests(unittest.TestCase):
    def test_offline_authentication_keeps_cached_forge_profile(self):
        with patch.dict('os.environ', {'AIYA_SD_BACKEND': 'forge'}), \
             patch.object(settings, 'authenticate_user', return_value=None), \
             patch.object(settings.global_var, 'forge_available', False), \
             patch.object(model_profiles, 'load_config', return_value=CONFIG), \
             patch.object(model_profiles.catalog, 'cached', return_value=fixture()):
            result = stablecog.resolve_model_profile('anima', {}, CHANNEL)
        self.assertEqual(result['architecture'], 'anima')

    def test_explicit_other_backend_keeps_legacy_xl_and_rejects_anima(self):
        with patch.dict('os.environ', {'AIYA_SD_BACKEND': 'automatic'}), \
             patch.object(model_profiles, 'load_config', return_value=CONFIG), \
             patch.object(settings, 'authenticate_user') as authenticate:
            self.assertIsNone(stablecog.resolve_model_profile('xl', {}, CHANNEL))
            with self.assertRaisesRegex(model_profiles.ProfileError, 'require Forge'):
                stablecog.resolve_model_profile('anima', {}, CHANNEL)
        authenticate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
