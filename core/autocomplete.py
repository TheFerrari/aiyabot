import discord
from typing import Iterable, Callable, List

from core import settings


MAX_CHOICES = 25


def _normalize_to_str_list(items: Iterable) -> List[str]:
    """
    Ensure we always work with a list of strings.
    """
    normalized: List[str] = []
    for item in items:
        if item is None:
            continue
        # Some lists in settings can contain nested lists, so we only keep the
        # first element in those cases.
        if isinstance(item, list) and item:
            value = item[0]
        else:
            value = item
        if value is None:
            continue
        normalized.append(str(value))
    return normalized


def _filter_choices(query: str, items: Iterable[str]) -> List[str]:
    """
    Filter items based on the user's query and limit to MAX_CHOICES.
    """
    items_list = list(items)
    if not items_list:
        return []

    if not query:
        return items_list[:MAX_CHOICES]

    q = str(query).lower()
    return [it for it in items_list if q in it.lower()][:MAX_CHOICES]


def make_simple_autocomplete(get_items: Callable[[], Iterable[str]]):
    """
    Factory for simple autocomplete callbacks that:
    - Read items from a supplier function.
    - Normalize to strings.
    - Filter by the user's current value.
    """

    async def callback(ctx: discord.AutocompleteContext):
        items = _normalize_to_str_list(get_items())
        return _filter_choices(ctx.value, items)

    return callback


model_autocomplete = make_simple_autocomplete(
    lambda: settings.global_var.model_info
)

sampler_autocomplete = make_simple_autocomplete(
    lambda: settings.global_var.sampler_names
)

scheduler_autocomplete = make_simple_autocomplete(
    lambda: settings.global_var.scheduler_names
)

style_autocomplete = make_simple_autocomplete(
    lambda: settings.global_var.style_names
)

upscaler_autocomplete = make_simple_autocomplete(
    lambda: settings.global_var.upscaler_names
)


def _get_lora_names() -> Iterable[str]:
    """
    Build a unique list of LoRA names, preserving a single 'None' entry.
    """
    unique_loras: List[str] = []
    seen_none = False

    for lora in settings.global_var.lora_names:
        if isinstance(lora, list) and lora:
            lora_name = str(lora[0])
        else:
            if lora is None:
                continue
            lora_name = str(lora)

        lower_name = lora_name.lower()

        if lower_name == "none":
            if not seen_none:
                unique_loras.append(lora_name)
                seen_none = True
        elif lora_name not in unique_loras:
            unique_loras.append(lora_name)

    return unique_loras


def _get_extra_nets() -> Iterable[str]:
    """
    Build a unique list of extra networks, preserving a single 'None' entry.
    """
    unique_nets: List[str] = []
    seen_none = False

    for network in settings.global_var.extra_nets:
        if isinstance(network, list) and network:
            net = str(network[0])
        else:
            if network is None:
                continue
            net = str(network)

        lower_net = net.lower()

        if lower_net == "none":
            if not seen_none:
                unique_nets.append(net)
                seen_none = True
        elif net not in unique_nets:
            unique_nets.append(net)

    return unique_nets


def _get_hires_upscalers() -> Iterable[str]:
    """
    Normalize hires upscaler names to a simple list of strings.
    """
    return _normalize_to_str_list(settings.global_var.hires_upscaler_names)


lora_autocomplete = make_simple_autocomplete(_get_lora_names)
extra_net_autocomplete = make_simple_autocomplete(_get_extra_nets)
hires_autocomplete = make_simple_autocomplete(_get_hires_upscalers)


async def size_autocomplete(ctx: discord.AutocompleteContext):
    """
    Autocomplete for image sizes. Uses size_range when available,
    otherwise falls back to size_range_exceed. This keeps behaviour
    dynamic instead of depending on import-time state.
    """
    base_items = settings.global_var.size_range or settings.global_var.size_range_exceed
    items = _normalize_to_str_list(base_items)
    return _filter_choices(ctx.value, items)

