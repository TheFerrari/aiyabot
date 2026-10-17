import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from core import model_profiles as profiles


def fixture():
    return {
        'options': {
            'forge_preset': 'xl', 'sd_model_checkpoint': 'xl.safetensors',
            # Deliberately wrong UI history: never select this for Anima.
            'forge_checkpoint_anima': 'xl.safetensors',
            'forge_additional_modules_xl': [],
            'forge_additional_modules_anima': ['/srv/VAE/qwen.safetensors', '/srv/text_encoder/qwen.safetensors'],
            'anima_t2i_step': 32, 'anima_t2i_cfg': 4.0, 'anima_t2i_dcfg': 3.0,
            'anima_t2i_sampler': 'ER SDE', 'anima_t2i_scheduler': 'Beta',
            'anima_t2i_width': 0, 'anima_t2i_height': 0,
            'anima_i2i_step': 28, 'anima_i2i_cfg': 4.5, 'anima_i2i_dcfg': 2.5,
            'anima_t2i_hr_step': 16, 'anima_t2i_hr_cfg': 3.5, 'anima_t2i_hr_dcfg': 2.0,
        },
        'models': [{'title': 'xl.safetensors [aaaa]'}, {'title': 'anima.safetensors [bbbb]'}],
        'modules': [
            {'filename': '/srv/VAE/qwen.safetensors', 'model_name': 'qwen.safetensors'},
            {'filename': '/srv/text_encoder/qwen.safetensors', 'model_name': 'qwen.safetensors'},
        ],
        'samplers': [{'name': 'ER SDE'}, {'name': 'Euler a'}, {'name': 'LCM'}],
        'schedulers': [{'name': 'Beta'}, {'name': 'Automatic'}],
    }


CONFIG = {'default_architecture': 'xl', 'models': {'anima': {'architecture': 'anima'}}}
CHANNEL = dict(steps=4, width=1024, height=1024, guidance_scale=1,
               distilled_cfg_scale=1, sampler='LCM', scheduler='Automatic', max_steps=50)


class ProfileTests(unittest.TestCase):
    def resolve(self, **kwargs):
        return profiles.resolve(kwargs.get('data', fixture()), kwargs.get('config', CONFIG),
                                kwargs.get('checkpoint', 'anima'), kwargs.get('explicit', {}),
                                kwargs.get('channel', CHANNEL), kwargs.get('mode', 't2i'))

    def test_preset_replaces_xl_channel_and_ignores_ui_checkpoint_history(self):
        result = self.resolve()
        self.assertEqual(result['checkpoint'], 'anima.safetensors [bbbb]')
        self.assertEqual(result['parameters']['steps'], 32)
        self.assertEqual(result['parameters']['guidance_scale'], 4)
        self.assertEqual(result['parameters']['distilled_cfg_scale'], 3)
        self.assertEqual(result['parameters']['sampler'], 'ER SDE')
        self.assertEqual(result['parameters']['width'], 1024)
        self.assertFalse(result['inherit_prompt_defaults'])

    def test_explicit_model_preset_channel_precedence(self):
        config = deepcopy(CONFIG)
        config['models']['anima']['parameters'] = {'steps': 40, 'guidance_scale': 5}
        result = self.resolve(config=config, explicit={'guidance_scale': '6,5', 'scheduler': 'beta'})
        self.assertEqual(result['parameters']['steps'], 40)
        self.assertEqual(result['parameters']['guidance_scale'], 6.5)
        self.assertEqual(result['parameters']['scheduler'], 'Beta')

    def test_img2img_and_hires_use_their_own_presets(self):
        result = self.resolve(mode='i2i')
        self.assertEqual(result['parameters']['steps'], 28)
        self.assertEqual(result['parameters']['distilled_cfg_scale'], 2.5)
        self.assertEqual(result['hires'], {'hr_second_pass_steps': 16, 'hr_cfg': 3.5, 'hr_distilled_cfg': 2})
        explicit = self.resolve(explicit={'steps': 12, 'guidance_scale': 6, 'distilled_cfg_scale': 4})
        self.assertEqual(explicit['hires'], {'hr_second_pass_steps': 12, 'hr_cfg': 6, 'hr_distilled_cfg': 4})

    def test_switch_xl_anima_xl_has_no_shared_modules(self):
        first = self.resolve(checkpoint='xl')
        second = self.resolve()
        third = self.resolve(checkpoint='xl')
        self.assertEqual(first, third)
        self.assertEqual(third['override_settings']['forge_additional_modules'], [])
        self.assertEqual(len(second['override_settings']['forge_additional_modules']), 2)
        self.assertNotIn('anima_do_reference', third['override_settings'])
        self.assertEqual(profiles.generation_settings(second)['override_settings_restore_afterwards'], True)

    def test_legacy_xl_channel_values_precede_forge(self):
        result = self.resolve(checkpoint='xl')
        for name in profiles.PARAMETERS:
            self.assertEqual(result['parameters'][name], CHANNEL[name])
        self.assertEqual(result['hires']['hr_second_pass_steps'], CHANNEL['steps'])

    def test_channel_anima_profile_precedes_model_and_forge_but_not_command(self):
        channel = deepcopy(CHANNEL)
        channel['generation_profiles'] = {'anima': {'steps': 40, 'sampler': 'Euler a',
            'scheduler': 'Automatic', 'guidance_scale': 6, 'distilled_cfg_scale': 5}}
        config = deepcopy(CONFIG)
        config['models']['anima']['parameters'] = {'steps': 45}
        config['models']['anima']['hires'] = {'hr_second_pass_steps': 48}
        result = self.resolve(channel=channel, config=config)
        self.assertEqual(result['parameters']['steps'], 40)
        self.assertEqual(result['parameters']['sampler'], 'Euler a')
        self.assertEqual(result['parameters']['scheduler'], 'Automatic')
        self.assertEqual(result['parameters']['distilled_cfg_scale'], 5)
        self.assertEqual(result['hires'], {'hr_second_pass_steps': 40, 'hr_cfg': 6, 'hr_distilled_cfg': 5})
        explicit = self.resolve(channel=channel, config=config, explicit={'steps': 12})
        self.assertEqual(explicit['parameters']['steps'], 12)
        self.assertEqual(explicit['hires']['hr_second_pass_steps'], 12)
        self.assertEqual(explicit['parameter_sources']['steps'], 'command')
        self.assertEqual(self.resolve(checkpoint='xl', channel=channel)['parameters']['steps'], CHANNEL['steps'])

    def test_reset_masks_legacy_xl_values(self):
        channel = deepcopy(CHANNEL)
        channel['generation_profiles'] = {'xl': dict.fromkeys(profiles.PARAMETERS)}
        result = self.resolve(checkpoint='xl', channel=channel)
        self.assertEqual(result['parameters']['steps'], 24)
        self.assertEqual(result['parameters']['sampler'], 'Euler a')

    def test_module_validation_missing_and_ambiguous(self):
        data = fixture()
        data['modules'].pop()
        with self.assertRaisesRegex(profiles.ProfileError, 'Module not found'):
            self.resolve(data=data)
        config = deepcopy(CONFIG)
        config['models']['anima']['modules'] = ['qwen.safetensors', 'qwen.safetensors']
        with self.assertRaisesRegex(profiles.ProfileError, 'ambiguous'):
            self.resolve(config=config)
        data['options']['forge_additional_modules_anima'] = []
        with self.assertRaisesRegex(profiles.ProfileError, 'text encoder and VAE'):
            self.resolve(data=data)

    def test_limits_and_invalid_values(self):
        self.assertEqual(self.resolve(channel={**CHANNEL, 'max_steps': 20})['parameters']['steps'], 20)
        for explicit in [{'width': 1000}, {'steps': 0}, {'guidance_scale': 'nan'}, {'scheduler': 'made-up'}]:
            with self.subTest(explicit=explicit), self.assertRaises(profiles.ProfileError):
                self.resolve(explicit=explicit)

    def test_replay_is_serializable_and_independent_of_later_preset_changes(self):
        original = self.resolve()
        saved = json.loads(json.dumps(original))
        replay = profiles.update_snapshot(saved, {'steps': 10, 'distilled_cfg_scale': 5})
        self.assertEqual(original['parameters']['steps'], 32)
        self.assertEqual(saved, original)
        self.assertEqual(replay['hires']['hr_distilled_cfg'], 5)
        payload = profiles.generation_settings(replay)
        payload['override_settings']['forge_additional_modules'].clear()
        self.assertEqual(len(replay['override_settings']['forge_additional_modules']), 2)

    def test_zero_preset_values_leave_channel_values_and_bad_hires_reports_error(self):
        data = fixture()
        data['options']['anima_t2i_step'] = 0
        data['options']['anima_t2i_cfg'] = 0
        result = self.resolve(data=data)
        self.assertEqual(result['parameters']['steps'], CHANNEL['steps'])
        self.assertEqual(result['parameters']['guidance_scale'], CHANNEL['guidance_scale'])
        config = deepcopy(CONFIG)
        config['models']['anima']['hires'] = {'hr_second_pass_steps': 'nan'}
        with self.assertRaisesRegex(profiles.ProfileError, 'hr_second_pass_steps'):
            self.resolve(config=config)

    def test_model_identity_does_not_guess_from_name(self):
        self.assertEqual(profiles.model_key(r'C:\models\Anima.safetensors [ABCD]'), 'anima')
        self.assertEqual(self.resolve(checkpoint='xl')['architecture'], 'xl')
        with self.assertRaisesRegex(profiles.ProfileError, 'Checkpoint not found'):
            self.resolve(checkpoint='unknown')

    def test_config_reports_bad_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'profiles.json'
            config.write_text('{"default_architecture":"flux","models":{}}')
            with patch.dict('os.environ', {'AIYA_MODEL_PROFILES': str(config)}):
                with self.assertRaisesRegex(profiles.ProfileError, 'default_architecture'):
                    profiles.load_config()


class CatalogTests(unittest.TestCase):
    def test_offline_cache_and_backend_isolation(self):
        from unittest.mock import Mock
        session = Mock()
        responses = [fixture()['options'], fixture()['modules'], fixture()['models'], fixture()['samplers'], fixture()['schedulers']]
        session.get.side_effect = [Mock(json=Mock(return_value=x)) for x in responses]
        catalog = profiles.ForgeCatalog(ttl=0)
        first = catalog.read(session, 'http://first')
        session.get.side_effect = OSError('offline')
        self.assertEqual(catalog.read(session, 'http://first'), first)
        with self.assertRaises(profiles.ProfileError):
            catalog.read(session, 'http://second')
        first['options'].clear()
        self.assertTrue(catalog.read(session, 'http://first')['options'])


if __name__ == '__main__':
    unittest.main()
