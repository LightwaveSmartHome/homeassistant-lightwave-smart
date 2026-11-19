"""Lightwave actions, registered once when the integration loads."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.entity_platform import async_get_platforms

from .const import (
    DOMAIN,
    SERVICE_RECONNECT,
    SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS,
    SERVICE_SETBRIGHTNESS,
    SERVICE_UPDATE,
)
from .entity import async_lightwave_command
from .runtime import iter_loaded_runtimes

_LOGGER = logging.getLogger(__name__)

SET_BRIGHTNESS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("brightness"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register actions. They exist even when no config entry is loaded."""

    async def async_reconnect(call):
        """Reconnect every loaded Lightwave account."""
        _LOGGER.debug("Received service call reconnect")
        for _entry, runtime in iter_loaded_runtimes(hass):
            link = runtime.link
            try:
                await link.async_deactivate(source="service_handle_reconnect")
                await link.async_activate(
                    source="service_handle_reconnect",
                    connect_callback=link.async_get_hierarchy,
                )
            except Exception as err:
                raise HomeAssistantError(f"Could not reconnect to Lightwave: {err}") from err

    async def async_update_states(call):
        """Read states again and refresh loaded entities."""
        _LOGGER.debug("Received service call update states")
        for _entry, runtime in iter_loaded_runtimes(hass):
            try:
                await runtime.link.async_update_featureset_states()
            except Exception as err:
                raise HomeAssistantError(
                    f"Could not update Lightwave states: {err}"
                ) from err
            for ent in runtime.entities:
                if ent.hass is not None:
                    ent.async_schedule_update_ha_state(True)

    async def async_reset_enabled_status(call):
        """Reset enabled status to defaults."""
        _LOGGER.debug(
            "reset_enabled_status_to_defaults: Received service call reset enabled status to defaults"
        )
        entity_registry = er.async_get(hass)

        count = 0
        enabled_count = 0
        for entry, runtime in iter_loaded_runtimes(hass):
            entities = runtime.entities
            for entity_entry in er.async_entries_for_config_entry(
                entity_registry, entry.entry_id
            ):
                count += 1
                entity_object = None
                for ent in entities:
                    if getattr(ent, "unique_id", None) == entity_entry.unique_id:
                        entity_object = ent
                        break

                if entity_object is None:
                    _LOGGER.warning(
                        "reset_enabled_status_to_defaults: Could not find entity object for %s/%s",
                        entity_entry.entity_id,
                        entity_entry.unique_id,
                    )
                    continue

                default_disabled = (
                    not entity_object.entity_description.entity_registry_enabled_default
                )
                if entity_entry.disabled == default_disabled:
                    continue

                _LOGGER.info(
                    "reset_enabled_status_to_defaults: Entity %s/%s will be changed to disabled: %s",
                    entity_entry.entity_id,
                    entity_entry.unique_id,
                    default_disabled,
                )
                entity_registry.async_update_entity(
                    entity_entry.entity_id,
                    disabled_by=(
                        er.RegistryEntryDisabler.INTEGRATION if default_disabled else None
                    ),
                )
                enabled_count += 1

        _LOGGER.info(
            "reset_enabled_status_to_defaults: Entities have been reset to defaults: %s of %s",
            enabled_count,
            count,
        )

    async def async_set_brightness(call):
        """Set gen 2 brightness from the entity's own link."""
        entity_ids = call.data["entity_id"]
        level = int(round(call.data["brightness"] / 255 * 100))
        wanted = set(entity_ids)
        found = {}
        for platform in async_get_platforms(hass, DOMAIN):
            for entity_id, entity in getattr(platform, "entities", {}).items():
                if entity_id in wanted:
                    found[entity_id] = entity
        missing = [entity_id for entity_id in entity_ids if entity_id not in found]
        if missing:
            raise ServiceValidationError(
                f"Unknown Lightwave entity: {', '.join(missing)}"
            )
        for entity_id in entity_ids:
            entity = found[entity_id]
            featureset = getattr(entity, "_featureset", None)
            feature = None if featureset is None else featureset.features.get("dimLevel")
            if feature is None:
                raise ServiceValidationError(f"{entity_id} has no brightness control")
            await async_lightwave_command(
                entity._lwlink.async_write_feature(feature.id, level),
                f"Could not set brightness for {entity_id}",
            )

    hass.services.async_register(DOMAIN, SERVICE_RECONNECT, async_reconnect)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE, async_update_states)
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS,
        async_reset_enabled_status,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SETBRIGHTNESS,
        async_set_brightness,
        schema=SET_BRIGHTNESS_SCHEMA,
    )
