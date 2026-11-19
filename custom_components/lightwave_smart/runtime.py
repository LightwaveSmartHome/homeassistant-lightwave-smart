"""Runtime objects for one loaded Lightwave config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN


@dataclass
class LightwaveRuntimeData:
    """Link and entities that exist while a config entry is loaded."""

    link: Any
    entities: list = field(default_factory=list)
    platforms: list = field(default_factory=list)
    link_repair_unsub: Any = None
    unavailable_logged: bool = False


def get_runtime(entry: ConfigEntry) -> LightwaveRuntimeData | None:
    """Return runtime data when this entry has been loaded."""
    try:
        data = entry.runtime_data
    except (AttributeError, RuntimeError, ValueError):
        return None
    if isinstance(data, LightwaveRuntimeData):
        return data
    return None


def iter_loaded_runtimes(hass: HomeAssistant):
    """Yield loaded config entries and their runtime data."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        runtime = get_runtime(entry)
        if runtime is not None:
            yield entry, runtime
