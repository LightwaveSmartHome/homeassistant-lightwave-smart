"""Shared Lightwave entity behaviour."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity import Entity


class LightwaveEntity(Entity):
    """Base entity for a Lightwave feature.

    States are pushed. The entity is unavailable while the websocket is down.
    A missing feature value stays unknown, which is handled by each platform.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _subscribe_featureset = True

    @property
    def available(self) -> bool:
        """Return False while the Lightwave websocket is down."""
        link = getattr(self, "_lwlink", None)
        if link is None:
            return False
        return bool(getattr(link, "connected", False))

    async def async_added_to_hass(self) -> None:
        """Subscribe to feature updates once the entity is registered."""
        await super().async_added_to_hass()
        if self._subscribe_featureset and getattr(self, "_featureset_id", None):
            await self.async_subscribe_feature(self._featureset_id)

    async def async_subscribe_feature(self, featureset_id: str) -> None:
        """Register a feature callback and drop it when the entity is removed."""
        await self._lwlink.async_register_feature_callback(
            featureset_id, self.async_update_callback
        )
        self.async_on_remove(
            lambda fid=featureset_id: drop_feature_callback(
                self._lwlink, fid, self.async_update_callback
            )
        )

    async def async_subscribe_general(self) -> None:
        """Register a link-wide callback and drop it when the entity is removed."""
        await self._lwlink.async_register_general_callback(self.async_update_callback)
        self.async_on_remove(
            lambda: drop_general_callback(self._lwlink, self.async_update_callback)
        )


def _drop(callbacks, callback) -> None:
    """Remove one callback. The library lists have no unregister method."""
    if not callbacks:
        return
    try:
        callbacks.remove(callback)
    except ValueError:
        return


def drop_feature_callback(link, featureset_id: str, callback) -> None:
    """Remove a feature callback."""
    featureset = getattr(link, "featuresets", {}).get(featureset_id)
    if featureset is None:
        return
    _drop(getattr(featureset, "_event_callbacks", None), callback)


def drop_general_callback(link, callback) -> None:
    """Remove a general link callback."""
    _drop(getattr(link, "_callbacks", None), callback)


def drop_firmware_callback(link, device_id: str, callback) -> None:
    """Remove a firmware event callback."""
    device = getattr(link, "devices", {}).get(device_id)
    if device is None:
        return
    _drop(getattr(device, "_firmware_event_callbacks", None), callback)


async def async_lightwave_command(action, message: str):
    """Await a Lightwave call and raise HomeAssistantError if it fails."""
    try:
        return await action
    except (HomeAssistantError, ServiceValidationError):
        raise
    except Exception as err:
        raise HomeAssistantError(f"{message}: {err}") from err
