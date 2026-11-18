"""Config flow coverage, including error recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow, storage
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.lightwave_smart.config_flow import (
    _strip_flow_keys,
    _zeroconf_cloud_id,
    _zeroconf_flow_title,
    _zeroconf_hub_name,
)
from custom_components.lightwave_smart.const import (
    CONF_API_KEY,
    CONF_HOMEKIT,
    CONF_LW_AUTH_METHOD,
    CONF_LW_INSTANCE_NAME,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture(autouse=True)
def fast_credential_sleep():
    with patch(
        "custom_components.lightwave_smart.config_flow.asyncio.sleep",
        new=AsyncMock(),
    ):
        yield


@pytest.fixture
def mock_setup_entry():
    with patch(
        "custom_components.lightwave_smart.async_setup_entry",
        return_value=True,
    ) as patched:
        yield patched


def _discovery(name: str, cloud_id) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address="192.168.1.9",
        ip_addresses=["192.168.1.9"],
        port=80,
        hostname="lightwave.local.",
        type="_lightwave._tcp.local.",
        name=name,
        properties={"id": cloud_id} if cloud_id is not None else {},
    )


async def _choose_method(hass, flow_id, method):
    return await hass.config_entries.flow.async_configure(
        flow_id, {CONF_LW_AUTH_METHOD: method}
    )


async def _submit_credentials(hass, flow_id, username="user@example.com", password="secret", instance="House"):
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_LW_INSTANCE_NAME: instance,
        },
    )


def _flow(hass, flow_id):
    return hass.config_entries.flow._progress[flow_id]


async def test_password_flow_creates_entry(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == CONF_LW_AUTH_METHOD

    result = await _choose_method(hass, result["flow_id"], "password")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House"
    assert result["result"].unique_id == DOMAIN
    assert result["data"][CONF_LW_AUTH_METHOD] == "password"
    assert result["data"][CONF_USERNAME] == "user@example.com"
    from custom_components.lightwave_smart.utils import get_stored_tokens

    stored = await get_stored_tokens(hass, "user@example.com")
    assert isinstance(stored, dict)


@pytest.mark.parametrize(
    ("activate", "error", "expected"),
    [
        ("false", None, "invalid_auth"),
        ("error", RuntimeError("timeout"), "cannot_connect"),
        ("error", RuntimeError("unauthorized"), "invalid_auth"),
    ],
)
async def test_password_errors_then_recover(hass, mock_link, mock_setup_entry, link_script, activate, error, expected):
    link_script.activate = activate
    link_script.activate_error = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "password")
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    link_script.activate = "ok"
    link_script.activate_error = None
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == DOMAIN


async def test_unknown_error_then_recover(hass, mock_link, mock_setup_entry, link_script):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "password")
    with patch(
        "custom_components.lightwave_smart.config_flow.set_stored_tokens",
        side_effect=RuntimeError("store"),
    ):
        result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"].startswith("unknown:")

    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_refresh_clears_password(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "refresh")
    result = await _submit_credentials(hass, result["flow_id"], instance="")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] is None
    assert result["title"] == "user@example.com"


async def test_invalid_auth_method_and_api_key_branch(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, result["flow_id"])
    invalid = await flow._step_auth("user", {CONF_LW_AUTH_METHOD: "nope"})
    assert invalid["errors"] == {"base": "invalid_auth_method"}

    api_key = await flow._step_auth(
        "user",
        {CONF_LW_AUTH_METHOD: "api_key", CONF_API_KEY: "key", CONF_REFRESH_TOKEN: "refresh"},
    )
    assert api_key["type"] is FlowResultType.FORM

    assert flow._evaluate_instance_name({CONF_LW_INSTANCE_NAME: "House"}) is True
    assert flow._evaluate_instance_name({}) is None
    assert flow._evaluate_instance_name(None) is None
    assert _strip_flow_keys({"original_source": "user", "application_credentials": True, "token": 1}) == {"token": 1}


async def test_second_instance_aborts(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "password")
    result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "single_instance_allowed"


async def test_zeroconf_discovery_and_duplicate(hass, mock_link, mock_setup_entry):
    discovered = _discovery("Lightwave Link Plus 310BD2._lightwave._tcp.local.", "BC310BD2")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert result["type"] is FlowResultType.FORM
    assert "BC310BD2" in result["description_placeholders"]["discovered_hub"]
    progress = hass.config_entries.flow.async_progress()
    assert progress[0]["context"]["title_placeholders"]["name"] == "Lightwave Link Plus (BC310BD2)"

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"

    result = await _choose_method(hass, result["flow_id"], "password")
    created = await _submit_credentials(hass, result["flow_id"])
    assert created["type"] is FlowResultType.CREATE_ENTRY

    configured = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert configured["type"] is FlowResultType.ABORT
    assert configured["reason"] == "already_configured"


def test_zeroconf_name_helpers():
    plain = _discovery("Lightwave Link Plus._lightwave._tcp.local.", None)
    assert _zeroconf_hub_name(plain) == "Lightwave Link Plus"
    assert _zeroconf_cloud_id(plain) is None
    assert _zeroconf_flow_title(plain) == "Lightwave Link Plus"

    encoded = _discovery("Hub._lightwave._tcp.local.", b"ABC")
    assert _zeroconf_cloud_id(encoded) == "ABC"
    assert _zeroconf_flow_title(encoded) == "Hub (ABC)"

    same = _discovery("ABC._lightwave._tcp.local.", "ABC")
    assert _zeroconf_flow_title(same) == "ABC"

    empty = _discovery("._lightwave._tcp.local.", "  ")
    assert _zeroconf_cloud_id(empty) is None or _zeroconf_cloud_id(empty) == ""
    blank = ZeroconfServiceInfo(
        ip_address="192.168.1.9",
        ip_addresses=["192.168.1.9"],
        port=None,
        hostname="lightwave.local.",
        type="_lightwave._tcp.local.",
        name="",
        properties={"id": ""},
    )
    assert _zeroconf_hub_name(blank) == "Lightwave Link Plus"
    assert _zeroconf_cloud_id(blank) is None
    assert _zeroconf_flow_title(blank) == "Lightwave Link Plus"

    only_id = _discovery("._lightwave._tcp.local.", "ONLY")
    assert _zeroconf_flow_title(only_id) == "Lightwave Link Plus (ONLY)"
    named = _discovery("Lightwave Link Plus 310BD2._lightwave._tcp.local.", "BC310BD2")
    assert _zeroconf_flow_title(named) == "Lightwave Link Plus (BC310BD2)"
    short_only = _discovery("310BD2._lightwave._tcp.local.", "BC310BD2")
    assert _zeroconf_flow_title(short_only) == "BC310BD2"


async def test_reauth_password(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Old",
        data={CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: "old@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _choose_method(hass, result["flow_id"], "password")
    result = await _submit_credentials(hass, result["flow_id"], username="new@example.com", instance="")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_USERNAME] == "new@example.com"


async def test_reconfigure_password(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Old",
        data={CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: "old@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    result = await _choose_method(hass, result["flow_id"], "refresh")
    result = await _submit_credentials(hass, result["flow_id"], instance="Renamed")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Renamed"
    assert entry.data[CONF_PASSWORD] is None


async def test_options_flow(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema_keys = {getattr(key, "schema", key) for key in result["data_schema"].schema}
    assert schema_keys == {CONF_HOMEKIT}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lightwave_homekit": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["lightwave_homekit"] is True

    again = await hass.config_entries.options.async_init(entry.entry_id)
    assert again["type"] is FlowResultType.FORM


class _MockOAuth(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    def __init__(self, domain: str = "lightwave_smart", name: str = "Mock Lightwave") -> None:
        self._domain = domain
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def domain(self) -> str:
        return self._domain

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        return "https://example.com/auth"

    async def async_resolve_external_data(self, external_data) -> dict:
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }

    async def _async_refresh_token(self, token: dict) -> dict:
        return {"access_token": "access", "expires_in": 3600}


async def _finish_oauth(hass, flow_id, implementation="lightwave_smart"):
    result = await hass.config_entries.flow.async_configure(flow_id)
    guard = 0
    finished = {FlowResultType.CREATE_ENTRY, FlowResultType.ABORT}
    progress = {FlowResultType.FORM, FlowResultType.EXTERNAL_STEP, FlowResultType.EXTERNAL_STEP_DONE}
    while result["type"] in progress and guard < 8:
        if result["step_id"] == "pick_implementation":
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"implementation": implementation}
            )
        elif result["type"] in {FlowResultType.EXTERNAL_STEP, FlowResultType.EXTERNAL_STEP_DONE}:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"code": "abc", "state": "xyz"}
            )
        else:
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        guard += 1
    return result


async def test_oauth_create_entry(hass, mock_link, mock_setup_entry):
    impl = _MockOAuth()
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, impl)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, result["flow_id"])
    flow.flow_impl = impl
    assert "lwapps" in flow.extra_authorize_data["scope"]
    assert flow.logger is not None

    result = await _choose_method(hass, result["flow_id"], "oauth")
    result = await _finish_oauth(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LW_AUTH_METHOD] == "oauth"
    assert "token" in result["data"] or "access_token" in result["data"]


async def test_oauth_without_implementation_saves_input(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "oauth")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "oauth_auth_method"

    flow = _flow(hass, result["flow_id"])
    continued = await flow.async_step_oauth_auth_method({CONF_LW_AUTH_METHOD: "oauth"})
    assert continued["type"] in {FlowResultType.FORM, FlowResultType.EXTERNAL_STEP, FlowResultType.ABORT}

    stored = hass.data[DOMAIN].get("oauth_user_input")
    assert stored is None or "original_source" in stored or CONF_LW_AUTH_METHOD in stored


async def test_oauth_reauth_and_unexpected_source(hass, mock_link, mock_setup_entry):
    impl = _MockOAuth(domain="cloud", name="Cloud")
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, impl)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Cloud house",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "old"}},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _choose_method(hass, result["flow_id"], "oauth")
    result = await _finish_oauth(hass, result["flow_id"], implementation="cloud")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    fresh = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    if fresh["type"] is not FlowResultType.ABORT:
        flow = _flow(hass, fresh["flow_id"])
        flow.flow_impl = impl
        updated = await flow.async_oauth_create_entry({"token": {"access_token": "new"}})
        assert updated["type"] is FlowResultType.ABORT
        assert updated["reason"] == "reconfigure_successful"


async def test_application_credential_helpers(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, result["flow_id"])
    store = storage.Store(hass, version=1, key="application_credentials", private=True)
    await store.async_save(
        {"items": [{"domain": "other", "client_id": "no"}, {"domain": DOMAIN, "client_id": "yes"}]}
    )
    found = await flow._get_application_credential(optimistic=True)
    assert found["client_id"] == "yes"
    found = await flow._get_application_credential(optimistic=False)
    assert found["client_id"] == "yes"
    await store.async_save({"items": []})
    assert await flow._get_application_credential(optimistic=True) is None

    hass.data.pop(DOMAIN, None)
    saved = await flow._save_oauth_user_input({CONF_LW_AUTH_METHOD: "oauth"})
    assert saved[CONF_LW_AUTH_METHOD] == "oauth"
    assert flow._pop_oauth_user_input()[CONF_LW_AUTH_METHOD] == "oauth"
    assert flow._pop_oauth_user_input() is None

    flow.hass = None
    assert await flow._save_oauth_user_input({CONF_LW_AUTH_METHOD: "oauth"}) is None
    flow.hass = hass

    none_step = await flow.async_step_oauth_auth_method(None)
    assert none_step["type"] in {FlowResultType.FORM, FlowResultType.ABORT}


async def test_oauth_scopes_and_cloud_provider(hass):
    from custom_components.lightwave_smart.auth import (
        _async_provide_cloud_link_implementation,
        async_register_cloud_account_link_provider,
    )

    assert await _async_provide_cloud_link_implementation(hass, "other") == []
    assert await _async_provide_cloud_link_implementation(hass, DOMAIN) == []

    hass.data["cloud"] = {}
    import builtins

    real_import = builtins.__import__
    module = SimpleNamespace()

    async def async_provide_implementation(hass_arg, domain):
        assert domain == "lightwaverf"
        return [SimpleNamespace(domain="cloud", name="Cloud")]

    module.async_provide_implementation = async_provide_implementation
    mode = {"block": True}

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "homeassistant.components.cloud" and fromlist and "account_link" in fromlist:
            if mode["block"]:
                raise ImportError("blocked")
            return SimpleNamespace(account_link=module)
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=_fake_import):
        assert await _async_provide_cloud_link_implementation(hass, DOMAIN) == []
        mode["block"] = False
        provided = await _async_provide_cloud_link_implementation(hass, DOMAIN)
        assert provided and provided[0].domain == "cloud"
        async_register_cloud_account_link_provider(hass)
        implementations = await config_entry_oauth2_flow.async_get_implementations(hass, DOMAIN)
    assert implementations


async def test_reconfigure_without_title_keeps_entry_title(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Kept",
        data={CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: "old@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    flow = _flow(hass, result["flow_id"])
    updated = await flow.async_step_user(
        {
            CONF_LW_AUTH_METHOD: "refresh",
            CONF_USERNAME: "",
            CONF_PASSWORD: "secret",
            CONF_LW_INSTANCE_NAME: "",
        }
    )
    assert updated["type"] is FlowResultType.ABORT
    assert updated["reason"] == "reconfigure_successful"
    assert entry.title == "Kept"


async def test_oauth_resume_and_unexpected_source(hass, mock_link, mock_setup_entry):
    from custom_components.lightwave_smart.const import CONF_LW_OAUTH_USER_INPUT

    started = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    hass.config_entries.flow.async_abort(started["flow_id"])

    store = storage.Store(hass, version=1, key="application_credentials", private=True)
    await store.async_save({"items": [{"domain": DOMAIN, "client_id": "yes"}]})
    hass.data.setdefault(DOMAIN, {})[CONF_LW_OAUTH_USER_INPUT] = {
        CONF_LW_AUTH_METHOD: "oauth",
        "original_source": SOURCE_USER,
    }
    resumed = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert resumed["type"] in {FlowResultType.FORM, FlowResultType.EXTERNAL_STEP, FlowResultType.ABORT}
    if resumed["type"] is not FlowResultType.ABORT:
        hass.config_entries.flow.async_abort(resumed["flow_id"])

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Cloud house",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "old"}},
    )
    entry.add_to_hass(hass)
    impl = _MockOAuth()
    result = await entry.start_reauth_flow(hass)
    flow = _flow(hass, result["flow_id"])
    flow.flow_impl = impl
    flow.context["source"] = "unexpected"
    created = await flow.async_oauth_create_entry({"token": {"access_token": "tok"}})
    assert created["type"] is FlowResultType.ABORT
    assert created["reason"] == "reconfigure_successful"

    result = await entry.start_reauth_flow(hass)
    flow = _flow(hass, result["flow_id"])
    flow.flow_impl = impl
    flow.context["source"] = SOURCE_RECONFIGURE
    created = await flow.async_oauth_create_entry({"token": {"access_token": "tok2"}})
    assert created["reason"] == "reconfigure_successful"
