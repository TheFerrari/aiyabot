"""Resolve Forge presets into immutable-by-convention, JSON serializable jobs.

No dependency on Discord or the bot's global settings. Callers supply an
already authenticated session; all paths come from the backend's module list.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import threading
import time


class ProfileError(ValueError):
    pass


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'model_profiles.json'
PARAMETERS = {
    'steps': 'step', 'sampler': 'sampler', 'scheduler': 'scheduler',
    'guidance_scale': 'cfg', 'distilled_cfg_scale': 'dcfg',
    'width': 'width', 'height': 'height',
}
# Defaults for missing preset fields, not replacements for customized presets.
FALLBACKS = {
    'xl': dict(steps=24, sampler='Euler a', scheduler='Automatic',
               guidance_scale=4.5, distilled_cfg_scale=9.0),
    'anima': dict(steps=32, sampler='ER SDE', scheduler='Beta',
                  guidance_scale=4.0, distilled_cfg_scale=3.0),
}


def model_key(value):
    """Match API titles, filenames and model_name without architecture guesses."""
    value = re.sub(r'\s+\[[0-9a-fA-F]+\]$', '', value or '')
    value = value.replace('\\', '/').rsplit('/', 1)[-1]
    return re.sub(r'\.(safetensors|ckpt|gguf)$', '', value, flags=re.I).casefold()


def load_config():
    path = Path(os.getenv('AIYA_MODEL_PROFILES', str(DEFAULT_CONFIG)))
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('default_architecture') not in FALLBACKS:
            raise ProfileError('default_architecture must be xl or anima')
        if not isinstance(config.get('models'), dict):
            raise ProfileError('models must be an object')
        for name, entry in config['models'].items():
            if not isinstance(entry, dict) or entry.get('architecture') not in FALLBACKS:
                raise ProfileError(f'Invalid architecture for {name}')
            if not isinstance(entry.get('parameters', {}), dict):
                raise ProfileError(f'Invalid parameters for {name}')
            if set(entry.get('parameters', {})) - PARAMETERS.keys():
                raise ProfileError(f'Unknown parameters for {name}')
            if not isinstance(entry.get('hires', {}), dict):
                raise ProfileError(f'Invalid hires parameters for {name}')
            if set(entry.get('hires', {})) - {'hr_second_pass_steps', 'hr_cfg', 'hr_distilled_cfg'}:
                raise ProfileError(f'Unknown hires parameters for {name}')
            if 'modules' in entry and (not isinstance(entry['modules'], list) or
                                       not all(isinstance(module, str) for module in entry['modules'])):
                raise ProfileError(f'Invalid modules for {name}')
            if 'dtype' in entry and not isinstance(entry['dtype'], str):
                raise ProfileError(f'Invalid dtype for {name}')
            if 'inherit_prompt_defaults' in entry and not isinstance(entry['inherit_prompt_defaults'], bool):
                raise ProfileError(f'Invalid inherit_prompt_defaults for {name}')
        return config
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ProfileError(f'Cannot read model profiles at {path}: {exc}') from exc


def model_config(config, checkpoint):
    for name, entry in config['models'].items():
        if model_key(name) == model_key(checkpoint):
            return entry
    return {'architecture': config['default_architecture']}


class ForgeCatalog:
    """Cache capabilities/presets per backend; retain the last good offline copy."""
    def __init__(self, ttl=60):
        self.ttl = ttl
        self._cache = {}
        self._lock = threading.Lock()

    def cached(self, url):
        with self._lock:
            previous = self._cache.get(url.rstrip('/'))
            return deepcopy(previous[1]) if previous else None

    def read(self, session, url, force=False):
        url = url.rstrip('/')
        with self._lock:
            previous = self._cache.get(url)
            if previous and not force and time.monotonic() - previous[0] < self.ttl:
                return deepcopy(previous[1])
            try:
                def get(path):
                    response = session.get(url + path, timeout=10)
                    response.raise_for_status()
                    return response.json()
                options = get('/sdapi/v1/options')
                if 'forge_preset' not in options:
                    result = None
                else:
                    result = {
                        'options': options,
                        'modules': get('/sdapi/v1/sd-modules'),
                        'models': get('/sdapi/v1/sd-models'),
                        'samplers': get('/sdapi/v1/samplers'),
                        'schedulers': get('/sdapi/v1/schedulers'),
                    }
                self._cache[url] = (time.monotonic(), result)
                return deepcopy(result)
            except Exception as exc:
                if previous and previous[1] is not None:
                    return deepcopy(previous[1])
                raise ProfileError(f'Cannot read Forge presets: {exc}') from exc


catalog = ForgeCatalog()


def numeric(name, value, integer=False, positive=False):
    try:
        number = float(str(value).replace(',', '.'))
        if not math.isfinite(number) or number < 0 or (positive and number == 0):
            raise ValueError()
        if integer and not number.is_integer():
            raise ValueError()
        return int(number) if integer else number
    except (ValueError, TypeError):
        raise ProfileError(f'Invalid {name}: {value}') from None


def channel_parameters(channel, architecture):
    """Legacy channel sampling values belong to XL; dimensions are shared.

    An explicit null in an architecture profile means 'use automatic defaults'
    and masks legacy values without deleting the channel's historical settings.
    """
    keys = PARAMETERS if architecture == 'xl' else ('width', 'height')
    values = {name: channel[name] for name in keys if name in channel}
    overrides = channel.get('generation_profiles', {}).get(architecture, {})
    if not isinstance(overrides, dict):
        raise ProfileError(f'Invalid channel profile: {architecture}')
    values.update({name: value for name, value in overrides.items() if name in PARAMETERS})
    return values


def resolve(catalog_data, config, checkpoint, explicit, channel, mode='t2i'):
    """Precedence: command > channel architecture > model > Forge defaults.

    A zero dimension in Forge means "leave unchanged", just as in its GUI.
    Always use the requested checkpoint; forge_checkpoint_<arch> is UI history.
    """
    if mode not in {'t2i', 'i2i'}:
        raise ProfileError(f'Unknown generation mode: {mode}')
    options = catalog_data['options']
    checkpoint = checkpoint or options.get('sd_model_checkpoint')
    matches = [m for m in catalog_data['models'] if model_key(m['title']) == model_key(checkpoint)]
    if len(matches) != 1:
        raise ProfileError(f'Checkpoint not found or ambiguous in Forge: {checkpoint}')
    checkpoint = matches[0]['title']
    entry = model_config(config, checkpoint)
    arch = entry['architecture']
    if arch not in FALLBACKS:
        raise ProfileError(f'Unsupported architecture: {arch}')
    available = catalog_data['modules']
    modules = []
    requested = entry.get('modules', options.get(f'forge_additional_modules_{arch}', []))
    if not isinstance(requested, list):
        raise ProfileError(f'Modules for {arch} must be a list')
    for module in requested:
        matches = [m['filename'] for m in available if module == m['filename']]
        if not matches and '/' not in module and '\\' not in module:
            matches = [m['filename'] for m in available if module.replace('\\', '/').rsplit('/', 1)[-1] == m['model_name']]
        if len(matches) != 1:
            raise ProfileError(f'Module not found or ambiguous in Forge: {module}')
        if matches[0] not in modules:
            modules.append(matches[0])
    # Require both components; a successful HTTP response alone can still yield
    # a blank image when an Anima checkpoint has no encoder or VAE selected.
    if arch == 'anima' and len(modules) < 2:
        raise ProfileError('Configure the Anima text encoder and VAE in Forge or model_profiles.json')

    channel_values = channel_parameters(channel, arch)
    params = {}
    sources = {}
    for name, suffix in PARAMETERS.items():
        value = explicit.get(name)
        source = 'command'
        if value is None:
            value, source = channel_values.get(name), 'channel'
        if value is None:
            value, source = entry.get('parameters', {}).get(name), 'model'
        if value is None:
            value = options.get(f'{arch}_{mode}_{suffix}', FALLBACKS[arch].get(name))
            source = 'forge'
            # Zero means 'do not change this UI value' in Forge's preset handler.
            if name not in {'sampler', 'scheduler'} and value is not None and numeric(name, value) == 0:
                value = channel.get(name)
                source = 'channel_fallback'
        if value is None:
            value = channel.get(name)
            source = 'channel_fallback'
        if name in {'sampler', 'scheduler'}:
            choices = catalog_data[name + 's']
            names = [c['name'] if isinstance(c, dict) else c for c in choices]
            match = next((n for n in names if n.casefold() == str(value).casefold()), None)
            if match is None:
                raise ProfileError(f'Unsupported {name}: {value}')
            value = match
        else:
            value = numeric(name, value, integer=name in {'steps', 'width', 'height'},
                            positive=name in {'steps', 'width', 'height'})
        params[name] = value
        sources[name] = source
    max_steps = channel.get('max_steps')
    if max_steps:
        params['steps'] = min(params['steps'], int(max_steps))
    for dim in ('width', 'height'):
        if params[dim] % 64:
            raise ProfileError(f'Forge dimensions must be multiples of 64: {dim}={params[dim]}')
    hires = {}
    for target, suffix, source in [('hr_second_pass_steps', 'step', 'steps'),
                                    ('hr_cfg', 'cfg', 'guidance_scale'),
                                    ('hr_distilled_cfg', 'dcfg', 'distilled_cfg_scale')]:
        value = entry.get('hires', {}).get(target, options.get(f'{arch}_t2i_hr_{suffix}'))
        # A user-configured base value also applies to the second pass.
        if explicit.get(source) is not None or channel_values.get(source) is not None:
            value = params[source]
        if value is None or numeric(target, value) == 0:
            value = params[source]
        hires[target] = numeric(target, value, integer=suffix == 'step', positive=suffix == 'step')
    if max_steps:
        hires['hr_second_pass_steps'] = min(hires['hr_second_pass_steps'], int(max_steps))
    return {
        'architecture': arch, 'checkpoint': checkpoint, 'mode': mode,
        'parameters': params, 'parameter_sources': sources, 'hires': hires,
        'inherit_prompt_defaults': entry.get('inherit_prompt_defaults', arch == 'xl'),
        'override_settings': {
            'sd_model_checkpoint': checkpoint,
            'forge_additional_modules': modules,
            'forge_unet_storage_dtype': entry.get('dtype', options.get(f'forge_unet_storage_dtype_{arch}', 'Automatic')),
            **({'anima_do_reference': False} if arch == 'anima' else {}),
        },
    }


def generation_settings(profile):
    """Payload fragment shared by txt2img and img2img, with request local overrides."""
    return {
        'override_settings': deepcopy(profile['override_settings']),
        'override_settings_restore_afterwards': True,
    }


def backend_scheduler(scheduler, profile=None):
    """Resolve Automatic before Forge's case-sensitive hires sampling path.

    The API exposes the name 'automatic', but only the label 'Automatic'
    triggers Forge's model-aware fallback. Old replay snapshots can contain
    either spelling. Anima needs Normal rather than the legacy sigma schedule.
    """
    if str(scheduler).strip().casefold() == 'automatic':
        return 'Normal' if profile and profile['architecture'] == 'anima' else 'Automatic'
    return scheduler


def shift_label(profile):
    return 'Shift' if profile and profile['architecture'] == 'anima' else 'Distilled CFG Scale'


PARAMETER_INDICES = {
    'steps': 5, 'width': 6, 'height': 7, 'guidance_scale': 8,
    'sampler': 9, 'scheduler': 20, 'distilled_cfg_scale': 21,
}


def update_snapshot(profile, values):
    """Keep a replay's resolved values in sync without re-reading the preset."""
    profile = deepcopy(profile)
    for name, value in values.items():
        if name not in profile['parameters']:
            continue
        if str(value) != str(profile['parameters'][name]):
            target = {'steps': 'hr_second_pass_steps', 'guidance_scale': 'hr_cfg',
                      'distilled_cfg_scale': 'hr_distilled_cfg'}.get(name)
            if target:
                profile['hires'][target] = value
            if 'parameter_sources' in profile:
                profile['parameter_sources'][name] = 'edit'
        profile['parameters'][name] = value
    return profile
