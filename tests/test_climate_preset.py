"""Preset classification for Lightwave climates.

A normal temperature target stays Auto while valve position moves. A target
of 40°C remains the manual-position sentinel.
"""

from types import SimpleNamespace

import pytest

from custom_components.lightwave_smart.climate import (
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    DEFAULT_AUTO_TARGET_TEMP,
    LWRF2Climate,
)


def _features(values):
    return SimpleNamespace(
        features={
            name: SimpleNamespace(state=value) for name, value in values.items()
        }
    )


def sample_climate(target=190, valve=40, humidity=None, target_humidity=None):
    """Build a TRV that exercises async_update without a hub."""
    entity = object.__new__(LWRF2Climate)
    entity._thermostat = False
    entity._has_humidity = humidity is not None
    entity._last_tt = 19.0
    values = {
        "temperature": 165,
        "targetTemperature": target,
        "valveLevel": valve,
        "heatState": 1,
    }
    if humidity is not None:
        values["humidity"] = humidity
        values["targetHumidity"] = target_humidity
    entity._featureset = _features(values)
    return entity


@pytest.mark.asyncio
@pytest.mark.parametrize("valve", [0, 20, 40, 60, 80, 100, None])
async def test_temperature_control_remains_auto(valve):
    entity = sample_climate(valve=valve)
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt == 19.0


@pytest.mark.asyncio
async def test_missing_target_does_not_erase_remembered_target():
    entity = sample_climate(target=None, valve=100)
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt == 19.0


@pytest.mark.asyncio
async def test_latest_normal_target_is_remembered_at_partial_opening():
    entity = sample_climate(target=205, valve=40)
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt == 20.5


@pytest.mark.asyncio
@pytest.mark.parametrize("valve", [20, 40, 60, 80, 100])
async def test_percentage_mode_remains_available_at_existing_sentinel(valve):
    entity = sample_climate(target=400, valve=valve)
    await entity.async_update()
    assert entity.preset_mode == f"{valve}%"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "valve, expected_action",
    [
        (0, CURRENT_HVAC_IDLE),
        (40, CURRENT_HVAC_HEAT),
        (None, CURRENT_HVAC_IDLE),
    ],
)
async def test_valve_position_still_drives_hvac_action(valve, expected_action):
    entity = sample_climate(target=190, valve=valve)
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity.hvac_action == expected_action


@pytest.mark.asyncio
async def test_humidity_update_stays_auto_for_a_normal_target():
    entity = sample_climate(target=190, valve=40, humidity=55, target_humidity=50)
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity._humidity == 55
    assert entity._target_humidity == 50
    assert entity._last_tt == 19.0


@pytest.mark.asyncio
async def test_humidity_trv_percentage_preset_follows_sentinel():
    entity = sample_climate(target=400, valve=80, humidity=48, target_humidity=45)
    await entity.async_update()
    assert entity.preset_mode == "80%"
    assert entity._humidity == 48
    assert entity._target_humidity == 45
    assert entity._last_tt == 19.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call_for_heat, expected_action",
    [
        (1, CURRENT_HVAC_HEAT),
        (0, CURRENT_HVAC_IDLE),
        (None, CURRENT_HVAC_IDLE),
    ],
)
async def test_call_for_heat_thermostat_stays_auto(call_for_heat, expected_action):
    entity = object.__new__(LWRF2Climate)
    entity._thermostat = True
    entity._has_humidity = False
    entity._last_tt = 19.0
    entity._featureset = _features(
        {
            "temperature": 180,
            "targetTemperature": 190,
            "callForHeat": call_for_heat,
        }
    )
    await entity.async_update()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt == 19.0
    assert entity.hvac_action == expected_action


def test_known_normal_target_is_remembered_and_sentinel_is_not():
    entity = object.__new__(LWRF2Climate)
    entity._last_tt = None
    entity._target_temperature = 40
    entity._valve_level = 60
    entity._apply_preset_mode()
    assert entity.preset_mode == "60%"
    assert entity._last_tt is None

    entity._target_temperature = None
    entity._valve_level = 20
    entity._apply_preset_mode()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt is None

    entity._target_temperature = 21
    entity._apply_preset_mode()
    assert entity.preset_mode == "Auto"
    assert entity._last_tt == 21


@pytest.mark.asyncio
async def test_set_preset_auto_uses_fallback_when_no_prior_target_seen():
    writes = []

    async def fake_set_temperature(featureset_id, level):
        writes.append((featureset_id, int(level * 10)))

    entity = object.__new__(LWRF2Climate)
    entity._featureset_id = "trv-1"
    entity._lwlink = SimpleNamespace(
        async_set_temperature_by_featureset_id=fake_set_temperature
    )
    entity._last_tt = None
    entity._target_temperature = 40.0
    entity._valve_level = 80
    entity._apply_preset_mode()
    assert entity.preset_mode == "80%"

    await entity.async_set_preset_mode("Auto")
    assert entity._target_temperature == DEFAULT_AUTO_TARGET_TEMP
    assert entity._last_tt == DEFAULT_AUTO_TARGET_TEMP
    assert writes == [("trv-1", 200)]


@pytest.mark.asyncio
async def test_set_preset_auto_restores_remembered_target():
    writes = []

    async def fake_set_temperature(featureset_id, level):
        writes.append((featureset_id, int(level * 10)))

    entity = object.__new__(LWRF2Climate)
    entity._featureset_id = "trv-1"
    entity._lwlink = SimpleNamespace(
        async_set_temperature_by_featureset_id=fake_set_temperature
    )
    entity._last_tt = 18.5
    entity._target_temperature = 40.0

    await entity.async_set_preset_mode("Auto")
    assert entity._target_temperature == 18.5
    assert entity._last_tt == 18.5
    assert writes == [("trv-1", 185)]

