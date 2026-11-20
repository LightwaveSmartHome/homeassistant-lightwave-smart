"""Connectivity sensor for a Smart Series device's radio."""

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components.lightwave_smart.const import CONF_HOMEKIT
from tests.helpers.link import add_device, add_featureset
from tests.test_platforms import _eid, _setup

_PORCH = "s1-10-99+1"


def _add_porch(link):
    device = add_device(link, _PORCH, "L21", room="Porch")
    device.set_connection_status("connected")
    add_featureset(link, device, "s1-porch-fs", "Porch", {"switch": 0})


def _state(hass, entity_id):
    return hass.states.get(entity_id).state


async def test_connectivity_sensor_tracks_the_device_radio(hass, mock_link, monkeypatch):
    import lightwave_smart.link as link_mod

    monkeypatch.setattr(link_mod, "_LINK_OFFLINE_GRACE_SECS", 0)
    monkeypatch.setattr(link_mod, "_LINK_REFRESH_DEBOUNCE_SECS", 0)
    monkeypatch.setattr(link_mod, "_LINK_REFRESH_RETRY_SECS", 0)
    mock_link.after_prepare = _add_porch

    entry = await _setup(hass)
    link = entry.runtime_data.link
    device = link.devices[_PORCH]
    entity_id = _eid(hass, "binary_sensor", f"{_PORCH}_connectivity")
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entity_id)
    firmware = registry.async_get(_eid(hass, "update", f"{_PORCH}_firmware"))

    assert entity_id is not None
    assert _eid(hass, "binary_sensor", "s1-hubdev_connectivity") is None
    assert _eid(hass, "binary_sensor", "s1-gen1_connectivity") is None
    assert entity_entry.disabled_by is None
    assert entity_entry.entity_category == EntityCategory.DIAGNOSTIC
    assert entity_entry.original_device_class == BinarySensorDeviceClass.CONNECTIVITY
    assert firmware.device_id == entity_entry.device_id
    assert _state(hass, entity_id) == "on"

    sensor = next(
        entity
        for entity in entry.runtime_data.entities
        if getattr(entity, "unique_id", None) == f"{_PORCH}_connectivity"
    )
    assert sensor._async_changed in device._connection_status_callbacks

    device.set_connection_status("disconnected")
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "off"

    device.set_connection_status("unknown")
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "unavailable"

    device.set_connection_status(None)
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "unavailable"

    device.set_connection_status("connected")
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "on"

    link._ws.fire_disconnect()
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "unavailable"

    await link._ws.async_connect()
    await hass.async_block_till_done()
    assert _state(hass, entity_id) == "on"

    await link._link_socket_event_handler({
        "items": [{"payload": {"linkId": "99", "online": False}}]
    })
    await next(iter(link._link_grace_tasks.values()))
    await hass.async_block_till_done()
    assert device.connection_status == "unknown"
    assert _state(hass, entity_id) == "unavailable"

    async def send_disconnected(message, *args, **kwargs):
        if getattr(message, "opclass", None) != "deviceStatus":
            return []
        return [{
            "success": True,
            "itemId": item["itemId"],
            "payload": {"status": "disconnected"},
        } for item in message.get_items()]

    link._ws.async_sendmessage = send_disconnected
    await link._link_socket_event_handler({
        "items": [{"payload": {"linkId": "99", "online": True, "connectedSince": 5}}]
    })
    await next(iter(link._link_refresh_tasks.values()))
    await hass.async_block_till_done()
    assert device.connection_status == "disconnected"
    assert _state(hass, entity_id) == "off"

    callback = sensor._async_changed
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert callback not in device._connection_status_callbacks


async def test_failed_hub_restore_leaves_the_sensor_unavailable(hass, mock_link, monkeypatch):
    import lightwave_smart.link as link_mod

    monkeypatch.setattr(link_mod, "_LINK_OFFLINE_GRACE_SECS", 0)
    monkeypatch.setattr(link_mod, "_LINK_REFRESH_DEBOUNCE_SECS", 0)
    monkeypatch.setattr(link_mod, "_LINK_REFRESH_RETRY_SECS", 0)
    mock_link.after_prepare = _add_porch
    entry = await _setup(hass)
    link = entry.runtime_data.link
    entity_id = _eid(hass, "binary_sensor", f"{_PORCH}_connectivity")

    async def send_failure(message, *args, **kwargs):
        if getattr(message, "opclass", None) != "deviceStatus":
            return []
        return [{
            "success": False,
            "itemId": item["itemId"],
            "error": {"message": "down"},
        } for item in message.get_items()]

    link._ws.async_sendmessage = send_failure
    await link._link_socket_event_handler({
        "items": [{"payload": {"linkId": "99", "online": False}}]
    })
    await next(iter(link._link_grace_tasks.values()))
    await link._link_socket_event_handler({
        "items": [{"payload": {"linkId": "99", "online": True, "connectedSince": 8}}]
    })
    await next(iter(link._link_refresh_tasks.values()))
    await hass.async_block_till_done()

    assert link.devices[_PORCH].connection_status == "unknown"
    assert _state(hass, entity_id) == "unavailable"


async def test_connectivity_sensor_stays_visible_with_homekit(hass, mock_link):
    mock_link.after_prepare = _add_porch
    await _setup(hass, options={CONF_HOMEKIT: True})
    registry = er.async_get(hass)
    entity_id = _eid(hass, "binary_sensor", f"{_PORCH}_connectivity")
    assert registry.async_get(entity_id).hidden_by is None
