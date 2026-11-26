"""Config entry diagnostics."""

from __future__ import annotations

import json

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.lightwave_smart.const import (
    CONF_LW_AUTH_METHOD,
    CONF_LW_USER_ID,
    DOMAIN,
)
from custom_components.lightwave_smart.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.lightwave_smart.runtime import LightwaveRuntimeData
from custom_components.lightwave_smart.utils import set_stored_tokens
from tests.helpers.link import add_feature


async def test_loaded_entry_redacts_secrets(hass, hass_client, mock_link):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret-password",
            CONF_LW_USER_ID: "lw-user-1",
        },
        unique_id="user@example.com",
        title="user@example.com",
    )
    entry.add_to_hass(hass)
    await set_stored_tokens(
        hass,
        "user@example.com",
        {
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "token_expiry": 1234,
        },
    )
    device_reg = dr.async_get(hass)
    device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "gone")}
    )
    disabled = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LW_AUTH_METHOD: "oauth"},
        unique_id="lw-user-9",
        title="Lightwave Smart",
        disabled_by=ConfigEntryDisabler.USER,
    )
    disabled.add_to_hass(hass)
    twin = device_reg.async_get_or_create(
        config_entry_id=disabled.entry_id, identifiers={(DOMAIN, "s1-hub")}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other_domain", "x")}
    )
    link = entry.runtime_data.link
    add_feature(link, link.featuresets["s1-hub"], "locationLatitude", 51427334)
    add_feature(link, link.featuresets["s1-hub"], "locationLongitude", -769473)

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dumped = json.dumps(result)
    for secret in (
        "user@example.com",
        "secret-password",
        "secret-access",
        "secret-refresh",
        "51427334",
        "-769473",
    ):
        assert secret not in dumped

    data = result["entry"]["data"]
    assert data[CONF_USERNAME] == REDACTED
    assert data[CONF_PASSWORD] == REDACTED
    assert data[CONF_LW_AUTH_METHOD] == "password"
    assert data[CONF_LW_USER_ID] == "lw-user-1"
    assert result["entry"]["title"] == REDACTED
    assert result["entry"]["unique_id"] == REDACTED
    assert result["stored_tokens"] == {
        "api_key": False,
        "access_token": True,
        "refresh_token": True,
        "token_expiry": 1234,
    }

    link = result["link"]
    assert link["connected"] is True
    assert {"featureset_id": "s1-hub", "name": "Link Plus"} in link["hubs"]
    dimmer = link["devices"]["s1-dimmer"]
    assert dimmer["product_code"] == "L21"
    assert dimmer["firmware_version"] == "1.0.0"
    assert dimmer["featuresets"][0]["featureset_id"] == "s1-dimmer-a"
    assert dimmer["featuresets"][0]["features"]["dimLevel"] == 50
    assert link["devices"]["s1-raising"]["manufacturer_code"] == "error: manufacturer"
    assert link["devices"]["s1-hubdev"]["serial"] == "HUB"
    hub_features = link["devices"]["s1-hubdev"]["featuresets"][0]["features"]
    assert hub_features["locationLatitude"] == REDACTED
    assert hub_features["locationLongitude"] == REDACTED

    entries = {item["entry_id"]: item for item in result["all_entries"]}
    assert entries[entry.entry_id]["title"] == REDACTED
    assert entries[entry.entry_id]["state"] == "loaded"
    assert entries[disabled.entry_id]["title"] == "Lightwave Smart"
    assert entries[disabled.entry_id]["disabled_by"] == "user"

    hub_devices = [
        device
        for device in result["registry"]["devices"]
        if device["identifiers"] == ["s1-hub"]
    ]
    assert {device["config_entry_id"] for device in hub_devices} == {
        entry.entry_id,
        disabled.entry_id,
    }
    assert all(device["config_entry_exists"] for device in hub_devices)
    assert all(device["identifiers"] for device in result["registry"]["devices"])
    twin_row = next(device for device in hub_devices if device["id"] == twin.id)
    assert twin_row["entities"] == 0
    assert any(
        device["identifiers"] == ["gone"] and device["config_entry_id"] == entry.entry_id
        for device in result["registry"]["deleted_devices"]
    )


async def test_unloaded_oauth_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LW_AUTH_METHOD: "oauth",
            "auth_implementation": "lightwave_smart",
            CONF_TOKEN: {"access_token": "oauth-access", "refresh_token": "oauth-refresh"},
        },
        unique_id="lw-user-2",
        title="Cottage",
    )
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "oauth-access" not in json.dumps(result)
    assert result["entry"]["data"][CONF_TOKEN] == REDACTED
    assert result["entry"]["data"]["auth_implementation"] == "lightwave_smart"
    assert result["entry"]["title"] == "Cottage"
    assert result["entry"]["unique_id"] == "lw-user-2"
    assert result["stored_tokens"] == {
        "api_key": False,
        "access_token": False,
        "refresh_token": False,
        "token_expiry": None,
    }
    assert result["link"] is None


async def test_link_without_hubs_reports_error(hass):
    class BrokenLink:
        connected = False
        devices = {}

        def get_hubs(self):
            raise RuntimeError("no hierarchy")

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_LW_AUTH_METHOD: "api_key"})
    entry.add_to_hass(hass)
    entry.runtime_data = LightwaveRuntimeData(link=BrokenLink())

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["link"] == {
        "connected": False,
        "hubs": "error: no hierarchy",
        "devices": {},
    }
