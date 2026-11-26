"""Setup, unload, repairs, and services."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntryAuthFailed,
    ConfigEntryDisabler,
    ConfigEntryNotReady,
    ConfigEntryState,
)
from homeassistant.exceptions import HomeAssistantError
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
    duplicate_issue_id,
)
from custom_components.lightwave_smart.const import (
    CONF_API_KEY,
    CONF_LW_AUTH_METHOD,
    CONF_LW_HUB_SERIALS,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from custom_components.lightwave_smart.repairs import (
    RemoveDuplicateEntryFlow,
    async_create_fix_flow,
)
from custom_components.lightwave_smart.utils import set_stored_tokens
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from tests.helpers.errors import oauth_reauth_error
from tests.helpers.link import separate_hub_per_link


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
    assert entry.runtime_data.link is link_script.links[-1]

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
    entry.runtime_data.entities.append(dummy)

    await hass.services.async_call(DOMAIN, "update_states", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "reset_enabled_status_to_defaults", {}, blocking=True)

    link._ws.deactivate_error = RuntimeError("reconnect")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "reconnect", {}, blocking=True)
    link._ws.deactivate_error = None

    async_central_callback()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_link_repair_issue(hass, mock_link):
    entry = _entry(hass, _password_data())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    link = entry.runtime_data.link
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


async def test_same_login_twice_is_not_started(hass, mock_link):
    """Issue 86: a second entry for the same login gets a repair and no entities."""
    original = _entry(hass, _password_data(), entry_id="original")
    assert await hass.config_entries.async_setup(original.entry_id)
    await hass.async_block_till_done()
    assert original.state is ConfigEntryState.LOADED
    assert original.data[CONF_LW_HUB_SERIALS] == ["HUB"]

    ignored = MockConfigEntry(domain=DOMAIN, source=SOURCE_IGNORE, unique_id="discovery:X")
    ignored.add_to_hass(hass)
    duplicate = _entry(
        hass,
        {CONF_LW_AUTH_METHOD: "oauth", CONF_TOKEN: {"access_token": "a", "expires_at": 9_999_999_999}},
        entry_id="duplicate",
    )
    hass.config_entries.async_update_entry(duplicate, title="Lightwave Smart")

    async def _impl(hass_arg, config_entry):
        return type("Impl", (), {"name": "Mock", "domain": DOMAIN})()

    with patch(
        "custom_components.lightwave_smart.config_entry_oauth2_flow.async_get_config_entry_implementation",
        side_effect=_impl,
    ):
        assert not await hass.config_entries.async_setup(duplicate.entry_id)
    await hass.async_block_till_done()
    assert duplicate.state is ConfigEntryState.SETUP_ERROR
    assert er.async_entries_for_config_entry(er.async_get(hass), duplicate.entry_id) == []

    issue_id = duplicate_issue_id(duplicate.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "entry_title": "Lightwave Smart",
        "owner_title": "House",
    }

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert isinstance(flow, RemoveDuplicateEntryFlow)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = issue_id
    shown = await flow.async_step_init()
    assert shown["step_id"] == "confirm"
    assert shown["description_placeholders"]["owner_title"] == "House"
    done = await flow.async_step_confirm({})
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(duplicate.entry_id) is None
    assert hass.config_entries.async_get_entry(original.entry_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

    again = await flow.async_step_confirm({})
    assert again["type"] == "create_entry"
    other = await async_create_fix_flow(hass, "something_else", None)
    assert type(other) is ConfirmRepairFlow


async def test_disabled_later_duplicate_gets_repair_from_owner(hass, mock_link, link_script):
    """Issue 86: a disabled second entry is never set up, so the owner raises its repair."""
    original = _entry(hass, _password_data(), entry_id="original")
    duplicate = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LW_AUTH_METHOD: "oauth", CONF_TOKEN: {"access_token": "a"}},
        unique_id="lw-user-1",
        title="Lightwave Smart",
        entry_id="duplicate",
        disabled_by=ConfigEntryDisabler.USER,
    )
    duplicate.add_to_hass(hass)
    device_reg = dr.async_get(hass)
    twin = device_reg.async_get_or_create(
        config_entry_id=duplicate.entry_id, identifiers={(DOMAIN, "s1-hub")}
    )

    assert await hass.config_entries.async_setup(original.entry_id)
    await hass.async_block_till_done()
    assert original.state is ConfigEntryState.LOADED

    issue_id = duplicate_issue_id(duplicate.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "entry_title": "Lightwave Smart",
        "owner_title": "House",
    }
    assert issue.data == {"entry_id": "duplicate", "owner_entry_id": "original"}

    links_before = len(link_script.links)
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = issue_id
    done = await flow.async_step_confirm({})
    assert done["type"] == "create_entry"
    await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(duplicate.entry_id) is None
    assert device_reg.async_get(twin.id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert original.state is ConfigEntryState.LOADED
    assert len(link_script.links) == links_before + 1


async def test_running_later_entry_is_not_offered_for_removal(hass, mock_link):
    """An older entry re-enabled after a newer one started does not flag the newer one."""
    earlier = MockConfigEntry(
        domain=DOMAIN,
        data=_password_data(),
        unique_id="earlier",
        title="House",
        entry_id="earlier",
        disabled_by=ConfigEntryDisabler.USER,
    )
    earlier.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=earlier.entry_id, identifiers={(DOMAIN, "s1-hub")}
    )
    later = _entry(hass, _password_data(), entry_id="later")
    assert await hass.config_entries.async_setup(later.entry_id)
    await hass.async_block_till_done()
    assert later.state is ConfigEntryState.LOADED

    await hass.config_entries.async_set_disabled_by(earlier.entry_id, None)
    await hass.async_block_till_done()
    assert earlier.state is ConfigEntryState.LOADED
    assert later.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, duplicate_issue_id(later.entry_id)) is None


async def test_duplicate_repair_without_loaded_owner(hass, mock_link):
    owner = _entry(hass, _password_data(), entry_id="owner")
    duplicate = _entry(hass, _password_data(), entry_id="duplicate")
    flow = await async_create_fix_flow(
        hass,
        duplicate_issue_id(duplicate.entry_id),
        {"entry_id": duplicate.entry_id, "owner_entry_id": owner.entry_id},
    )
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = duplicate_issue_id(duplicate.entry_id)
    await flow.async_step_confirm({})
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(duplicate.entry_id) is None
    assert owner.state is ConfigEntryState.NOT_LOADED


async def test_earlier_disabled_entry_does_not_block(hass, mock_link):
    disabled = _entry(hass, _password_data(), entry_id="disabled")
    assert await hass.config_entries.async_setup(disabled.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_set_disabled_by(
        disabled.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()

    unregistered = _entry(hass, _password_data(), entry_id="unregistered")
    later = _entry(hass, _password_data(), entry_id="later")
    assert await hass.config_entries.async_setup(later.entry_id)
    await hass.async_block_till_done()
    assert later.state is ConfigEntryState.LOADED
    assert unregistered.state is ConfigEntryState.NOT_LOADED

    device_reg = dr.async_get(hass)
    hub = device_reg.async_get_device_by_identifier((DOMAIN, "s1-hub"), later.entry_id)
    device_reg.async_remove_device(hub.id)
    fresh = _entry(hass, _password_data(), entry_id="fresh")
    assert await hass.config_entries.async_setup(fresh.entry_id)
    await hass.async_block_till_done()
    assert fresh.state is ConfigEntryState.LOADED


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
    separate_hub_per_link(link_script)
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


async def test_unload_edges(hass, mock_link, link_script):
    separate_hub_per_link(link_script)
    missing = _entry(hass, _password_data(), entry_id="never")
    assert await async_unload_entry(hass, missing) is True

    entry = _entry(hass, _password_data())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.platforms.clear()
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
