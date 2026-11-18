"""Platform setup, actions, callbacks, and the HomeKit hide option."""

from datetime import datetime, timezone

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN, ATTR_HS_COLOR
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.lightwave_smart.climate import CURRENT_HVAC_OFF
from custom_components.lightwave_smart.const import CONF_HOMEKIT, CONF_LW_AUTH_METHOD, DOMAIN, LIGHTWAVE_ENTITIES, LIGHTWAVE_LINK2
from custom_components.lightwave_smart.light import (
    _off_led_name,
    _remove_light_entity,
    _remove_stale_led_entities,
)
from custom_components.lightwave_smart.select import (
    _COLOUR_SETUPS,
    _DEFAULT_OUTPUT_MAP,
    _map_matches_mode,
    _output_roles,
)
from custom_components.lightwave_smart.sensor import LWRF2Sensor
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _eid(hass, domain, unique_id):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


def _entities(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_ENTITIES]


def _link(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_LINK2]


async def _setup(hass, options=None, before=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
        },
        options=options or {},
        unique_id=DOMAIN,
        title="House",
    )
    entry.add_to_hass(hass)
    if before is not None:
        before(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _by_featureset(entities, featureset_id, cls_name):
    return next(
        entity
        for entity in entities
        if getattr(entity, "_featureset_id", None) == featureset_id and entity.__class__.__name__ == cls_name
    )


async def test_platforms_create_entities_and_actions(hass, mock_link):
    def before(hass, entry):
        registry = er.async_get(hass)
        registry.async_get_or_create(
            "light", DOMAIN, "s1-dimmer-b_offLed", config_entry=entry, suggested_object_id="old_off_led"
        )
        registry.async_get_or_create(
            "light", DOMAIN, "s1-dimmer-a_ledIndicator", config_entry=entry, suggested_object_id="old_indicator"
        )
        registry.async_get_or_create(
            "light", DOMAIN, "s1-mk4_offLed", config_entry=entry, suggested_object_id="mk4_off"
        )
        registry.async_get_or_create(
            "switch",
            DOMAIN,
            "s1-switch-fs_smartSwitch",
            config_entry=entry,
            hidden_by=er.RegistryEntryHider.INTEGRATION,
        )
        registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            "s1-window-fs_windowPosition",
            config_entry=entry,
            hidden_by=er.RegistryEntryHider.INTEGRATION,
        )

    entry = await _setup(hass, before=before)
    registry = er.async_get(hass)
    link = _link(hass, entry)
    entities = _entities(hass, entry)

    assert _eid(hass, "light", "s1-dimmer_offLed") == "light.old_off_led"
    assert registry.async_get_entity_id("light", DOMAIN, "s1-dimmer-a_ledIndicator") is None
    assert registry.async_get_entity_id("light", DOMAIN, "s1-mk4_offLed") is None
    assert _eid(hass, "light", "s1-mk4-fs_uiLEDOnColour") is not None
    assert _eid(hass, "light", "s1-mk4-fs_uiLEDOffColour") is not None
    switch_entry = registry.async_get(_eid(hass, "switch", "s1-switch-fs_smartSwitch"))
    assert switch_entry.hidden_by is None
    window_entry = registry.async_get(_eid(hass, "binary_sensor", "s1-window-fs_windowPosition"))
    assert window_entry.hidden_by is None
    assert registry.async_get(_eid(hass, "switch", "s1-gen1-fs_smartSwitch")).hidden_by is None

    switch_id = _eid(hass, "switch", "s1-switch-fs_smartSwitch")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    socket_id = _eid(hass, "switch", "s1-socket-fs_smartSocket")
    await hass.services.async_call("switch", "turn_on", {"entity_id": socket_id}, blocking=True)

    light_id = _eid(hass, "light", "s1-dimmer-a_smartLightSwitch")
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": light_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    await hass.services.async_call(DOMAIN, "set_brightness", {"entity_id": light_id, "brightness": 200}, blocking=True)
    await hass.services.async_call("light", "turn_off", {"entity_id": light_id}, blocking=True)

    clicks = []
    hass.bus.async_listen("lightwave_smart.click", lambda event: clicks.append(event))
    button = link.featuresets["s1-switch-fs"].features["uiButton"]
    button.decoded_obj = {"eventType": "Short", "presses": 1}
    link.featuresets["s1-switch-fs"].call_event_callbacks(button, 0, 1)
    pair = link.featuresets["s1-dimmer-a"].features["uiButtonPair"]
    pair.decoded_obj = {"upDown": "Up", "eventType": "Long", "presses": 1}
    link.featuresets["s1-dimmer-a"].call_event_callbacks(pair, 0, 512)
    pair.decoded_obj = {"upDown": "Down", "eventType": "Short", "presses": 3}
    link.featuresets["s1-dimmer-a"].call_event_callbacks(pair, 0, 1)
    await hass.async_block_till_done()
    assert clicks

    event_entity = _by_featureset(entities, "s1-dimmer-a", "LWRF2UIButton")
    event_entity.async_update_callback()
    assert "lwrf_switch" in event_entity.extra_state_attributes

    cover = _by_featureset(entities, "s1-cover-fs", "LWRF2Cover")
    assert cover.current_cover_position is None
    assert cover.is_closed is None
    assert cover.is_opening is False
    link.featuresets["s1-cover-fs"].features["threeWayRelay"]._state = 1
    assert cover.is_opening is True
    link.featuresets["s1-cover-fs"].features["threeWayRelay"]._state = 2
    assert cover.is_closing is True
    link.featuresets["s1-cover-fs"].features["threeWayRelay"]._state = "nope"
    assert cover.is_opening is False
    link.featuresets["s1-cover-fs"].features["threeWayRelay"]._state = None
    assert cover._relay() is None
    await cover.async_open_cover()
    await cover.async_close_cover()
    await cover.async_stop_cover()
    cover.async_update_callback()

    climate = _by_featureset(entities, "s1-trv-fs", "LWRF2Climate")
    await climate.async_set_temperature(**{ATTR_TEMPERATURE: 21})
    await climate.async_set_humidity(40)
    await climate.async_set_hvac_mode("off")
    await climate.async_set_hvac_mode("heat")
    await climate.async_set_preset_mode("60%")
    await climate.async_set_preset_mode("Auto")
    assert climate.min_temp == 0
    assert climate.max_temp == 40
    assert climate.preset_modes
    link.featuresets["s1-trv-fs"].features["targetTemperature"]._state = 400
    link.featuresets["s1-trv-fs"].features["valveLevel"]._state = 50
    await climate.async_update()
    assert climate.preset_mode == "Auto"
    link.featuresets["s1-trv-fs"].features["heatState"]._state = 0
    await climate.async_update()
    assert climate.hvac_mode == "off"
    assert climate.hvac_action == CURRENT_HVAC_OFF
    climate.async_update_callback()
    stat = _by_featureset(entities, "s1-stat-fs", "LWRF2Climate")
    assert stat.hvac_modes == ["heat"]
    assert stat.current_humidity is False
    assert stat.target_humidity is False
    link.featuresets["s1-stat-fs"].features["callForHeat"]._state = None
    await stat.async_update()
    link.featuresets["s1-stat-fs"].features["temperature"]._state = None
    await stat.async_update()
    from custom_components.lightwave_smart.climate import LWRF2Climate

    link.featuresets["s1-stat-fs"].features["callForHeat"]._state = None
    fresh_stat = LWRF2Climate("Thermostat", "s1-stat-fs", link)
    assert fresh_stat is not None

    lock = _by_featureset(entities, "s1-socket-fs", "LWRF2Lock")
    assert lock.is_locked is True
    await lock.async_unlock()
    assert lock.is_locked is False
    await lock.async_lock()
    lock.async_update_callback()

    select = _by_featureset(entities, "s1-driver-fs", "LWRF2ColourSetup")
    assert select.current_option == "Warm White Cool White (CCT)"
    await select.async_select_option("RGB")
    link.featuresets["s1-driver-fs"].features["colourOutputMap"]._state = _DEFAULT_OUTPUT_MAP[0]
    await select.async_select_option("White")
    link.featuresets["s1-driver-fs"].features["colourOutputMap"]._state = None
    await select.async_select_option("RGB with White")
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = "bad"
    await select.async_update()
    assert select.current_option is None
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = 99
    await select.async_update()
    assert select.current_option is None
    select.async_update_callback()
    assert select.extra_state_attributes

    driver = _by_featureset(entities, "s1-driver-fs", "LWRF2LEDDriver")
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = 0
    await driver.async_update()
    assert driver.color_mode == "brightness"
    assert driver.supported_color_modes == {"brightness"}
    await driver.async_turn_on(**{ATTR_BRIGHTNESS: 0})
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = 1
    await driver.async_update()
    assert driver.color_mode == "hs"
    driver._featureset.features["colourSaturation"]._state = "bad"
    await driver.async_turn_on(**{ATTR_HS_COLOR: (10, 0), ATTR_BRIGHTNESS: 10})
    driver._featureset.features["colourSaturation"]._state = 80
    driver._featureset.features["colourHue"]._state = None
    await driver.async_turn_on(**{ATTR_HS_COLOR: (20, 0)})
    driver._featureset.features["colourHue"]._state = 30
    driver._featureset.features["colourSaturation"]._state = 90
    await driver.async_turn_on(**{ATTR_HS_COLOR: (40, 10)})
    await driver.async_turn_on(**{ATTR_HS_COLOR: (40, 100)})
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = 3
    await driver.async_update()
    assert driver.color_mode == "color_temp"
    await driver.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 100, ATTR_BRIGHTNESS: 1})
    await driver.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 20000})
    link.featuresets["s1-driver-fs"].features["colourSetup"]._state = None
    await driver.async_update()
    assert "color_temp" in driver.supported_color_modes
    assert driver.min_color_temp_kelvin == 2000
    assert driver.max_color_temp_kelvin == 9800
    link.featuresets["s1-driver-fs"].features["dimLevel"]._state = None
    link.featuresets["s1-driver-fs"].features["colourTemperature"]._state = 0
    link.featuresets["s1-driver-fs"].features["colourHue"]._state = None
    await driver.async_update()
    assert driver.is_on is False or driver.brightness is None
    driver._brightness = 0
    driver._state = 1
    assert driver.is_on is False
    driver.async_update_callback(feature="uiButtonPair", new_value=1)
    assert driver.extra_state_attributes

    dimmer = _by_featureset(entities, "s1-dimmer-a", "LWRF2Light")
    await dimmer.async_set_rgb(0x010203)
    dimmer.async_update_callback(feature="uiButtonPair", new_value=1)
    link.featuresets["s1-dimmer-a"].features["dimLevel"]._state = None
    await dimmer.async_update()
    assert dimmer.brightness is None

    led = next(entity for entity in entities if getattr(entity, "feature_type", None) == "rgbColor")
    await led.async_turn_on(rgb_color=(10, 20, 30), brightness=100)
    await led.async_turn_off()
    led.async_update_callback(feature="rgbColor", feature_id=led._featureset.features["rgbColor"].id, new_value=0)
    led.async_update_callback(feature="rgbColor", feature_id="missing", new_value=0x112233)
    led.async_update_callback(feature="switch")
    await led.async_update()
    assert led.rgb_color
    assert led.supported_color_modes == {"rgb"}
    gang = next(entity for entity in entities if getattr(entity, "feature_type", None) == "uiLEDOnColour")
    await gang.async_turn_on(brightness=255)
    await gang.async_turn_off()
    assert gang.extra_state_attributes

    sensors = [
        entity
        for entity in entities
        if entity.__class__.__name__ == "LWRF2Sensor"
        and getattr(entity, "_featureset_id", None) == "s1-dimmer-a"
    ]
    by_key = {sensor.entity_description.key: sensor for sensor in sensors}
    link.featuresets["s1-dimmer-a"].features["power"]._state = None
    await by_key["power"].async_update()
    assert by_key["power"].native_value == 12
    link.featuresets["s1-dimmer-a"].features["power"]._state = 15
    await by_key["power"].async_update()
    assert by_key["power"].native_value == 15
    link.featuresets["s1-dimmer-a"].features["lightLevel"]._state = 50
    await by_key["lightLevel"].async_update()
    assert by_key["lightLevel"].native_value == 150
    link.featuresets["s1-dimmer-a"].features["temperature"]._state = 215
    await by_key["temperature"].async_update()
    assert by_key["temperature"].native_value == 21.5
    dawn = next(
        entity
        for entity in entities
        if entity.__class__.__name__ == "LWRF2Sensor" and entity.entity_description.key == "dawnTime"
    )
    assert isinstance(dawn.native_value, datetime)
    link.featuresets["s1-hub"].features["dawnTime"]._state = None
    await dawn.async_update()
    kept = dawn._state
    link.featuresets["s1-hub"].features["year"]._state = None
    link.featuresets["s1-hub"].features["dawnTime"]._state = 100
    await dawn.async_update()
    assert dawn._state == kept
    dawn._state = None
    await dawn.async_update()
    assert dawn._state is None
    link.featuresets["s1-hub"].features["year"]._state = "bad"
    link.featuresets["s1-hub"].features["month"]._state = 11
    link.featuresets["s1-hub"].features["day"]._state = 16
    assert dawn._dawn_dusk_datetime(10) is None
    link.featuresets["s1-hub"].features["year"]._state = 2026
    assert dawn._dawn_dusk_datetime(-1) is None
    assert dawn._dawn_dusk_datetime(86400) == datetime(2026, 11, 17, tzinfo=timezone.utc)
    assert dawn._feature_state("missing") is None
    assert dawn._dawn_dusk_datetime(None) is None
    dawn.async_update_callback()

    event_sensor = next(entity for entity in entities if entity.__class__.__name__ == "LWRF2EventSensor")
    before_value = event_sensor.native_value
    await event_sensor.async_update()
    assert event_sensor.native_value >= before_value
    event_sensor.hass = hass
    await event_sensor.async_added_to_hass()
    event_sensor.async_update_callback()
    binary = _by_featureset(entities, "s1-window-fs", "LWRF2BinarySensor")
    link.featuresets["s1-window-fs"].features["windowPosition"]._state = 0
    await binary.async_update()
    assert binary.is_on == 0
    binary.async_update_callback()
    cloud = next(entity for entity in entities if entity.__class__.__name__ == "LWRF2LinkBinarySensor")
    assert cloud.is_on is True
    cloud.hass = None
    cloud._async_periodic_refresh(None)
    cloud.hass = hass

    updates = [entity for entity in entities if entity.__class__.__name__ == "LWRF2Update"]
    update = next(entity for entity in updates if entity._device.device_id == "s1-dimmer")
    assert await update.async_release_notes() == "Fixes"
    update.release_summary = None
    assert await update.async_release_notes() == "None"
    update._device.firmware_version = "9.9.9"
    update.async_update_callback()
    saved_id = link.config_entry_id
    link.config_entry_id = None
    update._update_device_sw_version()
    link.config_entry_id = saved_id
    async def _stay_offline():
        update._device.connection_status = None
        return None

    update._device.async_refresh_connection_status = _stay_offline
    update._device.connection_status = None
    with pytest.raises(HomeAssistantError):
        await update.async_install(None, False)

    async def _refresh():
        update._device.connection_status = "connected"
        return "connected"

    update._device.async_refresh_connection_status = _refresh
    link._ws._connected = True
    await update.async_install("1.2.0", False)
    assert link._ws.sent
    update._device.firmware_update_in_progress = True

    class _Boom:
        @property
        def firmware_version(self):
            raise RuntimeError("fw")

        latest_firmware_version = "1"
        latest_firmware_release_summary = None
        firmware_update_in_progress = False

    original = update._device
    update._device = _Boom()
    update.async_update_callback()
    update._device = original

    assert _off_led_name([type("F", (), {"name": ""})()]) == "Off LED"
    assert _off_led_name([type("F", (), {"name": "  "})(), type("F", (), {"name": "Gang (primary)"})()]) == "Gang (primary) Off LED"
    _remove_light_entity(registry, "does-not-exist")
    _remove_stale_led_entities(registry, [type("F", (), {"featureset_id": "none"})()], "none_offLed")

    # Exercise a sensor constructed with a missing feature key.
    orphan = LWRF2Sensor("Orphan", "s1-dimmer-a", link, by_key["power"].entity_description, hass)
    assert orphan._feature_state("not-a-feature") is None


def test_colour_output_map_rules():
    assert _output_roles("nope") is None
    assert _output_roles(None) is None
    assert _map_matches_mode("nope", 0) is False
    for mode, value in enumerate(_DEFAULT_OUTPUT_MAP):
        assert _map_matches_mode(value, mode) is True
    assert _map_matches_mode(0, 4) is False
    assert _COLOUR_SETUPS


async def test_homekit_hides_cloud_features(hass, mock_link):
    def before(hass, entry):
        registry = er.async_get(hass)
        registry.async_get_or_create(
            "event",
            DOMAIN,
            "s1-switch-fs_uiButton",
            config_entry=entry,
            hidden_by=er.RegistryEntryHider.INTEGRATION,
        )
        registry.async_get_or_create(
            "update",
            DOMAIN,
            "s1-dimmer_firmware",
            config_entry=entry,
            hidden_by=er.RegistryEntryHider.INTEGRATION,
        )
        registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            "s1-window-fs_uiDigitalInput",
            config_entry=entry,
            hidden_by=er.RegistryEntryHider.INTEGRATION,
        )

    entry = await _setup(hass, options={CONF_HOMEKIT: True}, before=before)
    registry = er.async_get(hass)

    def hidden(domain, unique_id):
        entity_id = _eid(hass, domain, unique_id)
        assert entity_id is not None
        return registry.async_get(entity_id).hidden_by

    assert hidden("light", "s1-dimmer-a_smartLightSwitch") == er.RegistryEntryHider.INTEGRATION
    assert hidden("switch", "s1-switch-fs_smartSwitch") == er.RegistryEntryHider.INTEGRATION
    assert hidden("event", "s1-switch-fs_uiButton") is None
    assert hidden("event", "s1-dimmer-a_uiButtonPair") is None
    assert hidden("binary_sensor", "s1-window-fs_windowPosition") == er.RegistryEntryHider.INTEGRATION
    assert hidden("binary_sensor", "s1-window-fs_movement") == er.RegistryEntryHider.INTEGRATION
    assert hidden("binary_sensor", "s1-socket-fs_outletInUse") == er.RegistryEntryHider.INTEGRATION
    assert hidden("binary_sensor", "s1-stat-fs_callForHeat") == er.RegistryEntryHider.INTEGRATION
    assert hidden("binary_sensor", "s1-window-fs_uiDigitalInput") is None
    assert hidden("update", "s1-dimmer_firmware") is None
    assert hidden("light", "s1-mk4-fs_uiLEDOnColour") is None
    assert hidden("switch", "s1-gen1-fs_smartSwitch") is None
    assert hidden("binary_sensor", "s1-hub_cloudLink") is None
    assert hidden("climate", "s1-trv-fs_thermostat") is None
    assert hidden("cover", "s1-cover-fs_cover") is None
    assert hidden("lock", "s1-socket-fs_lock") is None
    assert hidden("select", "s1-driver-fs_colourSetup") is None
    assert entry.title == "House"


async def test_event_sensor_constructor_is_caught(hass, mock_link):
    entry = await _setup(hass)
    from custom_components.lightwave_smart import sensor as sensor_mod

    real = sensor_mod.LWRF2EventSensor

    class Boom(real):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("event")

    sensor_mod.LWRF2EventSensor = Boom
    try:
        await sensor_mod.async_setup_entry(hass, entry, lambda entities: None)
    finally:
        sensor_mod.LWRF2EventSensor = real
