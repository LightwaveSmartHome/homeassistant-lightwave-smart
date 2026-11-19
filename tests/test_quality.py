"""Bronze and Silver behaviour: availability, actions, and callback cleanup."""

from __future__ import annotations

import logging

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lightwave_smart.const import CONF_LW_AUTH_METHOD, DOMAIN
from custom_components.lightwave_smart.entity import (
    LightwaveEntity,
    async_lightwave_command,
    drop_feature_callback,
    drop_firmware_callback,
    drop_general_callback,
)
from custom_components.lightwave_smart.runtime import LightwaveRuntimeData, get_runtime


def _entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
        },
        unique_id="quality",
        title="House",
    )
    entry.add_to_hass(hass)
    return entry


def test_runtime_and_callback_helpers():
    class Bare:
        pass

    assert get_runtime(Bare()) is None
    bare = Bare()
    bare.runtime_data = "not-runtime"
    assert get_runtime(bare) is None
    bare.runtime_data = LightwaveRuntimeData(link=object())
    assert get_runtime(bare) is bare.runtime_data

    callback = lambda **_kwargs: None
    link = type("Link", (), {"featuresets": {}, "devices": {}, "_callbacks": None})()
    drop_feature_callback(link, "missing", callback)
    drop_firmware_callback(link, "missing", callback)
    drop_general_callback(link, callback)

    link.featuresets["none"] = type("FeatureSet", (), {})()
    drop_feature_callback(link, "none", callback)
    other = lambda **_kwargs: None
    featureset = type("FeatureSet", (), {"_event_callbacks": [callback, other]})()
    link.featuresets["fs"] = featureset
    drop_feature_callback(link, "fs", callback)
    drop_feature_callback(link, "fs", callback)
    assert featureset._event_callbacks == [other]

    link._callbacks = [callback, other]
    drop_general_callback(link, callback)
    drop_general_callback(link, callback)
    assert link._callbacks == [other]

    link.devices["none"] = type("Device", (), {})()
    drop_firmware_callback(link, "none", callback)
    device = type("Device", (), {"_firmware_event_callbacks": [callback, other]})()
    link.devices["dev"] = device
    drop_firmware_callback(link, "dev", callback)
    drop_firmware_callback(link, "dev", callback)
    assert device._firmware_event_callbacks == [other]

    entity = LightwaveEntity()
    assert entity.available is False
    entity._lwlink = type("Link", (), {"connected": True})()
    assert entity.available is True
    entity._lwlink.connected = False
    assert entity.available is False


async def test_command_helper_raises_home_assistant_error():
    async def ok():
        return 3

    assert await async_lightwave_command(ok(), "unused") == 3

    async def boom():
        raise RuntimeError("down")

    with pytest.raises(HomeAssistantError, match="Could not run"):
        await async_lightwave_command(boom(), "Could not run")

    async def already():
        raise HomeAssistantError("kept")

    with pytest.raises(HomeAssistantError, match="kept"):
        await async_lightwave_command(already(), "Could not run")

    async def invalid():
        raise ServiceValidationError("bad input")

    with pytest.raises(ServiceValidationError):
        await async_lightwave_command(invalid(), "Could not run")


async def test_link_drop_marks_entities_unavailable_once(hass, mock_link, caplog):
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    link = entry.runtime_data.link
    switch_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, "s1-switch-fs_smartSwitch"
    )
    cloud_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, "s1-hub_cloudLink"
    )
    assert hass.states.get(switch_id).state != "unavailable"
    assert hass.states.get(cloud_id).state == "on"

    caplog.set_level(logging.INFO, logger="custom_components.lightwave_smart")
    link._ws.fire_disconnect()
    link._ws.fire_disconnect()
    await hass.async_block_till_done()

    assert hass.states.get(switch_id).state == "unavailable"
    assert hass.states.get(cloud_id).state == "off"
    assert caplog.text.count("Lightwave connection lost") == 1

    link._ws._connected = True

    async def _fire_connect():
        for callback in list(link._ws._connect_callbacks):
            result = callback()
            if hasattr(result, "__await__"):
                await result

    await _fire_connect()
    await _fire_connect()
    await hass.async_block_till_done()

    assert hass.states.get(switch_id).state != "unavailable"
    assert hass.states.get(cloud_id).state == "on"
    assert caplog.text.count("Lightwave connection restored") == 1

    featureset = link.featuresets["s1-switch-fs"]
    assert featureset._event_callbacks
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert featureset._event_callbacks == []
    assert link._callbacks == []


async def test_actions_fail_clearly_and_register_once(hass, mock_link):
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "set_brightness")
    await hass.services.async_call(DOMAIN, "reconnect", {}, blocking=True)

    light_id = er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, "s1-dimmer-a_smartLightSwitch"
    )
    socket_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, "s1-socket-fs_smartSocket"
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_brightness",
            {"entity_id": "light.missing_light", "brightness": 10},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_brightness",
            {"entity_id": socket_id, "brightness": 10},
            blocking=True,
        )

    link = entry.runtime_data.link

    async def fail_write(*_args, **_kwargs):
        raise RuntimeError("write")

    link.async_write_feature = fail_write
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_brightness",
            {"entity_id": light_id, "brightness": 20},
            blocking=True,
        )

    async def fail_states(*_args, **_kwargs):
        raise RuntimeError("states")

    link.async_update_featureset_states = fail_states
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "update_states", {}, blocking=True)

    switch = next(
        entity
        for entity in entry.runtime_data.entities
        if getattr(entity, "unique_id", None) == "s1-switch-fs_smartSwitch"
    )

    async def fail_on(*_args, **_kwargs):
        raise RuntimeError("on")

    link.async_turn_on_by_featureset_id = fail_on
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "reconnect", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "update_states", {}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "reset_enabled_status_to_defaults", {}, blocking=True
    )
