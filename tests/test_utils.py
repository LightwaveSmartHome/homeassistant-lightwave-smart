"""Device info helpers and token storage."""

from types import SimpleNamespace

from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lightwave_smart.const import DOMAIN
from custom_components.lightwave_smart.utils import (
    _via_device_id,
    get_extra_state_attributes,
    get_stored_tokens,
    make_device_info,
    make_entity_device_info,
    set_stored_tokens,
)


def _entity(**kwargs):
    return SimpleNamespace(**kwargs)


async def test_token_storage_roundtrip(hass):
    assert await get_stored_tokens(hass, "nobody@example.com") == {}
    await set_stored_tokens(hass, "nobody@example.com", {"access_token": "a"})
    assert await get_stored_tokens(hass, "nobody@example.com") == {"access_token": "a"}


def test_via_device_id_early_returns():
    entity = _entity(_lwlink=SimpleNamespace(hass=None, config_entry_id=None))
    assert _via_device_id(entity, None, "self") is None
    assert _via_device_id(entity, "self", "self") is None
    assert _via_device_id(entity, "parent", "self") is None
    entity._lwlink.hass = object()
    assert _via_device_id(entity, "parent", "self") is None


async def test_device_info_branches(hass):
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-utils")
    entry.add_to_hass(hass)
    entry_id = entry.entry_id
    device = SimpleNamespace(
        device_id="s1-dev",
        product_code="L21",
        virtual_product_code="X",
        manufacturer_code="LightwaveRF",
        serial="SER",
        firmware_version="1.0.0",
        name="Lamp",
        room_name="Hall",
        is_hub=lambda: False,
    )
    featureset = SimpleNamespace(
        featureset_id="s1-fs",
        device=device,
        room_name=None,
        features={"switch": SimpleNamespace(state=1)},
        is_gen2=lambda: True,
    )
    link = SimpleNamespace(
        hass=hass,
        config_entry_id=entry_id,
        featuresets={"s1-fs": featureset},
        get_linkPlus_featureset_id=lambda _id: "missing-parent",
    )
    entity = _entity(
        _device=device,
        _featureset=featureset,
        _featureset_id="s1-fs",
        _lwlink=link,
        _gen2=True,
    )
    info = make_device_info(entity)
    assert info["model"] == "L21-X"
    assert info["suggested_area"] == "Hall"
    assert "via_device_id" not in info

    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(config_entry_id=entry_id, identifiers={(DOMAIN, "s1-hub")})
    device_entry = registry.async_get_or_create(config_entry_id=entry_id, identifiers={(DOMAIN, "s1-dev")})
    link.get_linkPlus_featureset_id = lambda _id: "s1-hub"
    info = make_device_info(entity, name="Custom")
    assert info["via_device_id"] == parent.id
    assert info["name"] == "Custom"

    entity_info = make_entity_device_info(entity, "Gang")
    assert entity_info["name"] == "Gang"
    assert entity_info["suggested_area"] == "Hall"
    assert entity_info["via_device_id"] == device_entry.id

    device.is_hub = lambda: True
    entity._gen2 = True
    hub_info = make_entity_device_info(entity, "Ignored")
    assert "name" not in hub_info

    entity._gen2 = False
    device.is_hub = lambda: False
    featureset.room_name = "Kitchen"
    device.room_name = None
    named = make_entity_device_info(entity)
    assert named["suggested_area"] == "Kitchen"
    assert get_extra_state_attributes(entity)["lwrf_switch"] == 1
