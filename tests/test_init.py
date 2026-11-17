"""Setup, unload, repairs, and services."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.helpers import config_entry_oauth2_flow, device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.util import dt as dt_util

from custom_components.lightwave_smart import (
    PLATFORMS,
    PLATFORMS_FIRMWARE,
    async_central_callback,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.lightwave_smart.__init__ import (
    _classify_connect_failure,
    _safe_deactivate_link,
    _schedule_link_repair,
)
from custom_components.lightwave_smart.const import (
    CONF_API_KEY,
    CONF_LW_AUTH_METHOD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    LIGHTWAVE_ENTITIES,
    LIGHTWAVE_LINK2,
    LIGHTWAVE_PLATFORMS,
)
from custom_components.lightwave_smart.utils import set_stored_tokens
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from tests.helpers.errors import oauth_reauth_error


def _entry(hass, data, options=None, entry_id="entry-1"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        options=options or {},
        unique_id=DOMAIN,
        title="House",
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


def _password_data(**extra):
    data = {
        CONF_LW_AUTH_METHOD: "password",
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
    }
    data.update(extra)
    return data


async def test_password_setup_unload_and_services(hass, mock_link, link_script):
    logging.getLogger("custom_components.lightwave_smart").setLevel(logging.DEBUG)
    await set_stored_tokens(
        hass,
        "user@example.com",
        {"access_token": "access", "refresh_token": "refresh", "token_expiry": 10},
    )
    device_reg = dr.async_get(hass)
    entry = _entry(hass, _password_data())
    device_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "gone")})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert device_reg.async_get_device_by_identifier((DOMAIN, "gone"), entry.entry_id) is None
    assert hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_LINK2] is link_script.links[-1]

    link = link_script.links[-1]
    refresh = getattr(link.auth, "_token_refresh_callback", None) or getattr(link.auth, "token_refresh_callback", None)
    if refresh is None:
        callback = getattr(link.auth, "_on_token_refresh", None)
        refresh = callback
    # The integration registers whatever callback the library stores. Invoke the
    # one passed to set_token_refresh_callback if the library kept it.
    stored = None
    for attr in dir(link.auth):
        value = getattr(link.auth, attr)
        if callable(value) and "refresh" in attr and not attr.startswith("set_"):
            stored = value
            break
    if stored is not None:
        try:
            stored("new-access", "new-refresh", 20)
            await hass.async_block_till_done()
        except TypeError:
            pass

    entity_reg = er.async_get(hass)
    rssi = entity_reg.async_get_entity_id("sensor", DOMAIN, "s1-dimmer-a_rssi")
    if rssi is not None:
        entity_reg.async_update_entity(rssi, disabled_by=None)
    entity_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "missing-object",
        config_entry=entry,
        suggested_object_id="missing_object",
    )
    dummy = type("Dummy", (), {"hass": None, "unique_id": "dummy"})()
    hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_ENTITIES].append(dummy)

    await hass.services.async_call(DOMAIN, "update_states", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "reset_enabled_status_to_defaults", {}, blocking=True)

    link._ws.deactivate_error = RuntimeError("reconnect")
    await hass.services.async_call(DOMAIN, "reconnect", {}, blocking=True)
    link._ws.deactivate_error = None

    async_central_callback()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_link_repair_issue(hass, mock_link):
    entry = _entry(hass, _password_data())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    link = hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_LINK2]
    issue_id = f"{entry.entry_id}_link_disconnected"

    link._ws.fire_disconnect()
    link._ws.fire_disconnect()
    link._ws._connected = True
    for callback in list(link._ws._connect_callbacks):
        result = callback()
        if hasattr(result, "__await__"):
            await result
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    link._ws.fire_disconnect()
    link._ws._connected = True
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    link._ws.fire_disconnect()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    link._ws._connected = True
    for callback in list(link._ws._connect_callbacks):
        result = callback()
        if hasattr(result, "__await__"):
            await result
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="link_disconnected",
    )
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_missing_credentials(hass, mock_link):
    entry = _entry(hass, {CONF_LW_AUTH_METHOD: "password", CONF_PASSWORD: "secret"})
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "refresh", CONF_USERNAME: "user@example.com"},
        entry_id="entry-refresh",
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "api_key", CONF_USERNAME: "user@example.com"},
        entry_id="entry-api",
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


async def test_refresh_and_api_key_setup(hass, mock_link, link_script):
    await set_stored_tokens(
        hass,
        "user@example.com",
        {"refresh_token": "refresh", "access_token": "access", "token_expiry": 5},
    )
    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "refresh", CONF_USERNAME: "user@example.com"},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await set_stored_tokens(
        hass,
        "api@example.com",
        {CONF_API_KEY: "key", CONF_REFRESH_TOKEN: "refresh", "access_token": "access"},
    )
    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "api_key", CONF_USERNAME: "api@example.com"},
        entry_id="entry-api-ok",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(link_script.links) >= 2


async def test_activate_failures(hass, mock_link, link_script):
    entry = _entry(hass, _password_data())
    link_script.activate = "false"
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)

    link_script.activate = "false"
    link_script.clear_access_token = True
    await set_stored_tokens(hass, "user@example.com", {"access_token": "access", "refresh_token": "refresh"})
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    link_script.clear_access_token = False
    link_script.activate = "error"
    link_script.activate_error = RuntimeError("down")
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)

    link_script.activate = "error"
    link_script.activate_error = ConfigEntryAuthFailed("already")
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    link_script.activate = "cancel"
    with pytest.raises(asyncio.CancelledError):
        await async_setup_entry(hass, entry)

    link_script.activate = "keyboard"
    with pytest.raises(KeyboardInterrupt):
        await async_setup_entry(hass, entry)


async def test_oauth_setup_failures(hass, mock_link, link_script):
    entry = _entry(hass, {CONF_LW_AUTH_METHOD: "oauth"})
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "oauth", CONF_TOKEN: {"access_token": "a", "expires_at": 9_999_999_999}},
        entry_id="oauth-1",
    )
    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=config_entry_oauth2_flow.ImplementationUnavailableError("missing"),
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    impl = type(
        "Impl",
        (),
        {
            "name": "Mock",
            "domain": "lightwave_smart",
        },
    )()

    async def _impl(hass_arg, config_entry):
        return impl

    link_script.activate = "false"
    link_script.token_rejected = True
    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=_impl,
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)


async def test_oauth_reauth_error_and_not_ready(hass, mock_link, link_script):
    entry = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "oauth", CONF_TOKEN: {"access_token": "a", "expires_at": 9_999_999_999}},
        entry_id="oauth-2",
    )

    async def _impl(hass_arg, config_entry):
        return type("Impl", (), {"name": "Mock", "domain": DOMAIN})()

    link_script.activate = "error"
    link_script.activate_error = oauth_reauth_error("reauth")
    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=_impl,
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)

    link_script.activate = "false"
    link_script.activate_error = None
    link_script.last_error = oauth_reauth_error("cached")
    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=_impl,
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)

    link_script.last_error = None
    link_script.token_rejected = False
    link_script.activate = "error"
    link_script.activate_error = RuntimeError("temporary")
    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=_impl,
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_refresh_token_dropped_after_connect(hass, mock_link, link_script):
    await set_stored_tokens(hass, "user@example.com", {"refresh_token": "refresh"})
    entry = _entry(hass, {CONF_LW_AUTH_METHOD: "refresh", CONF_USERNAME: "user@example.com"})
    link_script.activate = "false"
    link_script.drop_refresh_token = True
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


async def test_platform_setup_failure_deactivates(hass, mock_link):
    entry = _entry(hass, _password_data())
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()
    assert hass.data.get(DOMAIN, {}).get(entry.entry_id) is None


async def test_deactivate_error_is_swallowed():
    await _safe_deactivate_link(None, "none")

    class Boom:
        async def async_deactivate(self, source):
            raise RuntimeError("nope")

    await _safe_deactivate_link(Boom(), "boom")


async def test_unload_edges(hass, mock_link):
    missing = _entry(hass, _password_data(), entry_id="never")
    assert await async_unload_entry(hass, missing) is True

    entry = _entry(hass, _password_data())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][entry.entry_id][LIGHTWAVE_PLATFORMS] = []
    assert await async_unload_entry(hass, entry) is True
    await hass.config_entries.async_unload_platforms(entry, [*PLATFORMS_FIRMWARE, *PLATFORMS])
    await hass.async_block_till_done()

    entry = _entry(hass, _password_data(), entry_id="entry-unload")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with patch.object(hass.config_entries, "async_unload_platforms", side_effect=RuntimeError("unload")):
        assert await async_unload_entry(hass, entry) is False
    await hass.config_entries.async_unload_platforms(entry, [*PLATFORMS_FIRMWARE, *PLATFORMS])
    await hass.async_block_till_done()

    hass.data[DOMAIN] = None
    _schedule_link_repair(hass, "missing")
    stray = _entry(hass, _password_data(), entry_id="stray")
    await async_remove_entry(hass, stray)
    for platform in async_get_platforms(hass, DOMAIN):
        await platform.async_reset()
    await hass.async_block_till_done()


def test_classify_direct():
    class Auth:
        def __init__(self):
            self.last_error = None
            self.token_rejected = False
            self.tokens = {"refresh_token": "r", "access_token": "a"}

        def get_tokens(self):
            return self.tokens

    link = type("Link", (), {"auth": Auth()})()
    err = ConfigEntryNotReady("ready")
    assert _classify_connect_failure(link, "password", False, err) is err
    reauth = oauth_reauth_error("no")
    assert isinstance(_classify_connect_failure(link, "oauth", False, reauth), ConfigEntryAuthFailed)
    link.auth.last_error = reauth
    assert isinstance(_classify_connect_failure(link, "oauth", False, None), ConfigEntryAuthFailed)
    link.auth.last_error = None
    link.auth.token_rejected = True
    assert isinstance(_classify_connect_failure(link, "oauth", False, None), ConfigEntryAuthFailed)
    link.auth.token_rejected = False
    assert isinstance(_classify_connect_failure(link, "oauth", False, RuntimeError("x")), ConfigEntryNotReady)
    link.auth.tokens = {}
    assert isinstance(_classify_connect_failure(link, "refresh", False, None), ConfigEntryAuthFailed)
    link.auth.tokens = {"refresh_token": "r"}
    assert isinstance(_classify_connect_failure(link, "api_key", True, None), ConfigEntryNotReady)
    link.auth.tokens = {}
    assert isinstance(_classify_connect_failure(link, "password", True, None), ConfigEntryAuthFailed)
    link.auth.tokens = {"access_token": "a"}
    assert isinstance(_classify_connect_failure(link, "password", False, None), ConfigEntryNotReady)
