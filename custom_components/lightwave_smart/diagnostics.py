"""Diagnostics support for Lightwave Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)
from .runtime import get_runtime
from .utils import get_stored_tokens

TO_REDACT = {
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_API_KEY,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    "id_token",
}

FEATURES_TO_REDACT = {"locationLatitude", "locationLongitude"}


def _attr(obj: Any, name: str) -> Any:
    """Read an attribute from the Lightwave model without failing the download."""
    try:
        return getattr(obj, name, None)
    except Exception as err:  # noqa: BLE001
        return f"error: {err}"


def _without_email(value: Any) -> Any:
    """Redact titles and unique ids that are the account email."""
    if isinstance(value, str) and "@" in value:
        return REDACTED
    return value


def _feature_states(featureset: Any) -> dict[str, Any]:
    states = {}
    for feature_type, feature in (_attr(featureset, "features") or {}).items():
        states[feature_type] = (
            REDACTED if feature_type in FEATURES_TO_REDACT else _attr(feature, "state")
        )
    return states


def _device(device: Any) -> dict[str, Any]:
    return {
        "product_code": _attr(device, "product_code"),
        "virtual_product_code": _attr(device, "virtual_product_code"),
        "manufacturer_code": _attr(device, "manufacturer_code"),
        "serial": _attr(device, "serial"),
        "firmware_version": _attr(device, "firmware_version"),
        "latest_firmware_version": _attr(device, "latest_firmware_version"),
        "connection_status": _attr(device, "connection_status"),
        "featuresets": [
            {
                "featureset_id": _attr(featureset, "featureset_id"),
                "name": _attr(featureset, "name"),
                "features": _feature_states(featureset),
            }
            for featureset in _attr(device, "featuresets") or []
        ],
    }


def _link(link: Any) -> dict[str, Any]:
    try:
        hubs = [
            {"featureset_id": featureset_id, "name": name}
            for featureset_id, name in link.get_hubs()
        ]
    except Exception as err:  # noqa: BLE001
        hubs = f"error: {err}"
    return {
        "connected": _attr(link, "connected"),
        "hubs": hubs,
        "devices": {
            device_id: _device(device)
            for device_id, device in (_attr(link, "devices") or {}).items()
        },
    }


def _entries(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Summarise every Lightwave entry, including disabled and ignored ones."""
    return [
        {
            "entry_id": entry.entry_id,
            "title": _without_email(entry.title),
            "unique_id": _without_email(entry.unique_id),
            "source": entry.source,
            "state": entry.state.value,
            "disabled_by": entry.disabled_by,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in hass.config_entries.async_entries(DOMAIN)
    ]


def _lightwave_identifiers(device: Any) -> list[str]:
    # Identifiers are normally (domain, id). Other integrations on a shared
    # device can leave longer tuples, so do not unpack to exactly two items.
    return sorted(
        identifier[1]
        for identifier in device.identifiers
        if len(identifier) >= 2 and identifier[0] == DOMAIN
    )


def _registry(hass: HomeAssistant) -> dict[str, Any]:
    """List registry devices with Lightwave identifiers, whichever entry owns them."""
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    known_entries = {entry.entry_id for entry in hass.config_entries.async_entries()}

    devices = []
    for device in device_registry.devices.values():
        identifiers = _lightwave_identifiers(device)
        if not identifiers:
            continue
        entities = er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
        devices.append(
            {
                "id": device.id,
                "identifiers": identifiers,
                "config_entry_id": device.config_entry_id,
                "config_entry_exists": device.config_entry_id in known_entries,
                "name": device.name,
                "name_by_user": device.name_by_user,
                "model": device.model,
                "serial_number": device.serial_number,
                "disabled_by": device.disabled_by,
                "via_device_id": device.via_device_id,
                "created_at": device.created_at.isoformat(),
                "entities": len(entities),
                "disabled_entities": sum(
                    1 for entity in entities if entity.disabled_by is not None
                ),
            }
        )

    deleted_devices = [
        {
            "id": device.id,
            "identifiers": _lightwave_identifiers(device),
            "config_entry_id": device.config_entry_id,
            "orphaned_timestamp": device.orphaned_timestamp,
        }
        for device in device_registry.deleted_devices.values()
        if _lightwave_identifiers(device)
    ]

    return {"devices": devices, "deleted_devices": deleted_devices}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_dict = async_redact_data(entry.as_dict(), TO_REDACT)
    entry_dict["title"] = _without_email(entry_dict.get("title"))
    entry_dict["unique_id"] = _without_email(entry_dict.get("unique_id"))

    username = entry.data.get(CONF_USERNAME)
    stored = await get_stored_tokens(hass, username) if username else {}
    stored_tokens = {
        key: bool(stored.get(key))
        for key in (CONF_API_KEY, CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN)
    }
    stored_tokens[CONF_TOKEN_EXPIRY] = stored.get(CONF_TOKEN_EXPIRY)

    runtime = get_runtime(entry)
    return {
        "entry": entry_dict,
        "stored_tokens": stored_tokens,
        "all_entries": _entries(hass),
        "registry": _registry(hass),
        "link": _link(runtime.link) if runtime and runtime.link else None,
    }
