"""Config flow coverage, including error recovery."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow, storage
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.lightwave_smart.config_flow import (
    _account_label,
    _cloud_ids_from_data,
    _is_account_switch,
    _jwt_payload,
    _strip_flow_keys,
    _zeroconf_cloud_id,
    _zeroconf_flow_title,
    _zeroconf_hub_name,
    account_unique_id,
)
from custom_components.lightwave_smart.const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_HOMEKIT,
    CONF_LW_AUTH_METHOD,
    CONF_LW_CLOUD_IDS,
    CONF_LW_HUB_SERIALS,
    CONF_LW_USER_ID,
    CONF_LW_INSTANCE_NAME,
    CONF_LW_OAUTH_USER_INPUT,
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


async def _confirm_account_change(hass, flow_id):
    return await hass.config_entries.flow.async_configure(flow_id, {})


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
    assert result["result"].unique_id == "user@example.com"
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
    assert result["result"].unique_id == "user@example.com"


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
    assert result["step_id"] == "refresh"
    result = await _submit_credentials(hass, result["flow_id"], instance="")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] is None
    assert result["title"] == "user@example.com"
    assert result["result"].unique_id == "user@example.com"


def test_refresh_step_title_is_not_password_authentication():
    text = (
        Path(__file__).resolve().parents[1]
        / "custom_components/lightwave_smart/translations/en.json"
    ).read_text(encoding="utf-8")
    titles = json.loads(text)["config"]["step"]
    assert titles["refresh"]["title"] == "Lightwave Smart - Refresh Token"
    assert "Password" not in titles["refresh"]["title"]
    assert titles["user"]["title"] == "Lightwave Smart - Password Authentication"
    assert "Password Authentication Configuration" not in titles["reconfigure"]["title"]
    assert titles["reauth_confirm"]["title"] == "Lightwave Smart - Reauthenticate"
    assert titles["reauth_confirm"]["description"]
    assert titles["confirm_account_change"]["title"] == "Different Lightwave account"
    assert "{current_account}" in titles["confirm_account_change"]["description"]
    assert "{new_account}" in titles["confirm_account_change"]["description"]
    assert "single_instance_allowed" not in json.loads(text)["config"]["abort"]


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


async def test_second_account_migrates_legacy_entry(hass, mock_link, mock_setup_entry):
    legacy = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Old house",
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "old@example.com",
            CONF_PASSWORD: "old",
        },
    )
    duplicate = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        title="Duplicate legacy",
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "old@example.com",
            CONF_PASSWORD: "old",
        },
    )
    unnamed = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="No identity",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "opaque"}},
    )
    legacy.add_to_hass(hass)
    duplicate.add_to_hass(hass)
    unnamed.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert legacy.unique_id == "old@example.com"
    assert duplicate.unique_id is None
    assert unnamed.unique_id == DOMAIN

    result = await _choose_method(hass, result["flow_id"], "password")
    result = await _submit_credentials(
        hass, result["flow_id"], username="New@Example.com", instance="New"
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_additional_account"
    assert result["description_placeholders"]["existing_accounts"] == "Duplicate legacy, No identity"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "new@example.com"

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    again = await _choose_method(hass, again["flow_id"], "password")
    again = await _submit_credentials(
        hass, again["flow_id"], username="new@example.com", instance="Dup"
    )
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "already_configured"


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
    assert created["result"].unique_id == "user@example.com"
    assert created["result"].data[CONF_LW_CLOUD_IDS] == ["BC310BD2"]

    configured = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert configured["type"] is FlowResultType.ABORT
    assert configured["reason"] == "already_configured"

    other = _discovery("Lightwave Link Plus 999999._lightwave._tcp.local.", "BC999999")
    other_flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=other
    )
    assert other_flow["type"] is FlowResultType.FORM
    other_flow = await _choose_method(hass, other_flow["flow_id"], "password")
    other_flow = await _submit_credentials(hass, other_flow["flow_id"])
    assert other_flow["type"] is FlowResultType.ABORT
    assert other_flow["reason"] == "already_configured"
    assert created["result"].data[CONF_LW_CLOUD_IDS] == ["BC310BD2", "BC999999"]

    repeated = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=other
    )
    assert repeated["type"] is FlowResultType.ABORT
    assert repeated["reason"] == "already_configured"

    plain = _discovery("Lightwave Link Plus._lightwave._tcp.local.", None)
    unnamed = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=plain
    )
    assert unnamed["type"] is FlowResultType.FORM


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
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_account_change"
    assert result["description_placeholders"]["current_account"] == "old@example.com"
    assert result["description_placeholders"]["new_account"] == "new@example.com"
    result = await _confirm_account_change(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_USERNAME] == "new@example.com"
    assert entry.unique_id == "new@example.com"

    same = await entry.start_reauth_flow(hass)
    same = await hass.config_entries.flow.async_configure(same["flow_id"], {})
    same = await _choose_method(hass, same["flow_id"], "password")
    same = await _submit_credentials(
        hass, same["flow_id"], username="new@example.com", instance=""
    )
    assert same["type"] is FlowResultType.ABORT
    assert same["reason"] == "reauth_successful"

    taken = MockConfigEntry(
        domain=DOMAIN,
        unique_id="taken@example.com",
        title="Taken",
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "taken@example.com",
            CONF_PASSWORD: "old",
        },
    )
    taken.add_to_hass(hass)
    blocked = await entry.start_reauth_flow(hass)
    blocked = await hass.config_entries.flow.async_configure(blocked["flow_id"], {})
    blocked = await _choose_method(hass, blocked["flow_id"], "password")
    blocked = await _submit_credentials(
        hass, blocked["flow_id"], username="taken@example.com", instance=""
    )
    assert blocked["type"] is FlowResultType.ABORT
    assert blocked["reason"] == "already_configured"
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
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_account_change"
    result = await _confirm_account_change(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Renamed"
    assert entry.data[CONF_PASSWORD] is None
    assert entry.unique_id == "user@example.com"


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


def _jwt(payload: dict) -> str:
    def segment(value: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
        return raw

    return f"{segment({'alg': 'none'})}.{segment(payload)}.sig"


class _MockOAuth(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    def __init__(
        self,
        domain: str = "lightwave_smart",
        name: str = "Mock Lightwave",
        user_id: str | None = "oauth-user",
    ) -> None:
        self._domain = domain
        self._name = name
        self.user_id = user_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def domain(self) -> str:
        return self._domain

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        return "https://example.com/auth"

    async def async_resolve_external_data(self, external_data) -> dict:
        token = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }
        if self.user_id:
            token["id_token"] = _jwt({"sub": self.user_id})
        return token

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
        elif result["step_id"] == "confirm_additional_account":
            break
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
    assert result["title"] == "Mock Lightwave"
    assert result["result"].unique_id == "oauth-user"
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
    assert fresh["type"] is not FlowResultType.ABORT
    flow = _flow(hass, fresh["flow_id"])
    flow.flow_impl = impl
    updated = await flow.async_oauth_create_entry({"token": {"access_token": "new"}})
    assert updated["type"] is FlowResultType.FORM
    assert updated["step_id"] == "confirm_additional_account"
    assert updated["description_placeholders"]["existing_accounts"] == "Cloud house"
    updated = await flow.async_step_confirm_additional_account({})
    assert updated["type"] is FlowResultType.CREATE_ENTRY
    assert updated["title"] == "Lightwave Smart"
    assert flow.unique_id is None
    hass.config_entries.flow.async_abort(fresh["flow_id"])


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


def _raw_jwt(payload) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_account_switch_helpers():
    target = MockConfigEntry(
        domain=DOMAIN,
        unique_id="old@example.com",
        title="House",
        data={CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: "old@example.com"},
    )
    assert _is_account_switch(None, "new", {}, target) is False
    assert _is_account_switch("old", None, {}, target) is False
    assert _is_account_switch("same", "same", {}, target) is False
    assert _is_account_switch(
        "old@example.com",
        "user-id",
        {CONF_USERNAME: "old@example.com"},
        target,
    ) is False
    no_username = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a@b.c",
        title="House",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "x"}},
    )
    assert _is_account_switch(
        "a@b.c",
        "user-id",
        {CONF_USERNAME: "a@b.c"},
        no_username,
    ) is False
    assert _is_account_switch(
        "old@example.com",
        "new@example.com",
        {CONF_USERNAME: "new@example.com"},
        target,
    ) is True
    assert _is_account_switch("first", "third", {"token": {}}, target) is True
    assert _account_label(None, None, None) == "unknown"
    assert _account_label("id", {CONF_USERNAME: " A@B.C "}, "Title") == "a@b.c"
    assert _account_label("id", {CONF_LW_INSTANCE_NAME: " Holiday "}, "Title") == "Holiday"
    assert _account_label("id", {}, " Title ") == "Title"
    assert _account_label("id", {}, None) == "id"


def test_account_unique_id_helpers():
    assert account_unique_id(None) is None
    assert account_unique_id({CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: " A@B.C "}) == "a@b.c"
    user_id = "a01716f2-aba6-46b6-ba66-f57d0fc1a54a"
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "a@b.c",
            CONF_ACCESS_TOKEN: _jwt({"sub": user_id}),
        }
    ) == user_id
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "a@b.c",
            CONF_ACCESS_TOKEN: _jwt({"structureId": "struct-1"}),
        }
    ) == "a@b.c"
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "refresh",
            CONF_USERNAME: "a@b.c",
            CONF_ACCESS_TOKEN: "opaque",
            CONF_LW_USER_ID: f" {user_id} ",
        }
    ) == user_id
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "a@b.c",
            CONF_ACCESS_TOKEN: "",
            CONF_LW_USER_ID: "  ",
        }
    ) == "a@b.c"
    assert account_unique_id({CONF_LW_AUTH_METHOD: "refresh", CONF_USERNAME: "   "}) is None
    assert account_unique_id({CONF_LW_AUTH_METHOD: "password", CONF_USERNAME: None}) is None
    assert account_unique_id({CONF_USERNAME: 5}) is None

    user_token = _jwt({"sub": "user-1", "structureId": "ignored"})
    structure_token = _jwt({"structureId": "struct-1"})
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "oauth",
            "token": {"id_token": user_token, "access_token": structure_token},
        }
    ) == "user-1"
    assert account_unique_id(
        {CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": structure_token}}
    ) is None
    assert account_unique_id({"token": {"user": {"_id": "a01716f2-aba6-46b6-ba66-f57d0fc1a54a"}}}) == (
        "a01716f2-aba6-46b6-ba66-f57d0fc1a54a"
    )
    assert account_unique_id(
        {"token": {"structures": ["listed", {"structureId": "struct-1"}]}}
    ) is None
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "oauth",
            "token": {"refresh_token": "r"},
            "id_token": _jwt({"sub": "  ", "user": {"userId": "top-user"}}),
        }
    ) == "top-user"
    assert account_unique_id(
        {
            CONF_LW_AUTH_METHOD: "oauth",
            "token": {"id_token": "a.!!!.c", "access_token": _jwt({"user_id": "from-access"})},
        }
    ) == "from-access"
    assert _jwt_payload(_raw_jwt(["not", "a", "dict"])) is None
    assert _jwt_payload("not-a-jwt") is None
    assert _jwt_payload("a.") is None

    assert _cloud_ids_from_data(None) == []
    assert _cloud_ids_from_data({}) == []
    assert _cloud_ids_from_data({CONF_LW_CLOUD_IDS: " ABC "}) == ["ABC"]
    assert _cloud_ids_from_data({CONF_LW_CLOUD_IDS: "   "}) == []
    assert _cloud_ids_from_data({CONF_LW_CLOUD_IDS: ["a", "a", " b ", "", 3]}) == ["a", "b"]
    assert _cloud_ids_from_data({CONF_LW_CLOUD_IDS: 5}) == []


async def test_oauth_accounts_are_separate(hass, mock_link, mock_setup_entry):
    impl = _MockOAuth(user_id="one")
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, impl)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "oauth")
    result = await _finish_oauth(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "one"

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    again = await _choose_method(hass, again["flow_id"], "oauth")
    again = await _finish_oauth(hass, again["flow_id"])
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "already_configured"

    impl.user_id = "two"
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    second = await _choose_method(hass, second["flow_id"], "oauth")
    second = await _finish_oauth(hass, second["flow_id"])
    assert second["type"] is FlowResultType.CREATE_ENTRY
    assert second["result"].unique_id == "two"

    titled = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, titled["flow_id"])
    flow.flow_impl = _MockOAuth(name="Named", user_id="named")
    named = await flow.async_oauth_create_entry(
        {
            CONF_LW_INSTANCE_NAME: "Holiday",
            "token": {"id_token": _jwt({"sub": "named"})},
        }
    )
    assert named["type"] is FlowResultType.CREATE_ENTRY
    assert named["title"] == "Holiday"
    assert flow.unique_id == "named"
    hass.config_entries.flow.async_abort(titled["flow_id"])


async def test_oauth_create_restores_reauth_context(hass, mock_link, mock_setup_entry):
    first = MockConfigEntry(
        domain=DOMAIN,
        unique_id="first",
        title="First",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "a"}},
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        unique_id="second",
        title="Second",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "b"}},
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    started = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, started["flow_id"])
    flow.flow_impl = _MockOAuth(user_id="second")
    hass.data.setdefault(DOMAIN, {})[CONF_LW_OAUTH_USER_INPUT] = {
        "original_source": SOURCE_REAUTH,
        "entry_id": second.entry_id,
    }
    created = await flow.async_oauth_create_entry(
        {"token": {"id_token": _jwt({"sub": "second"})}}
    )
    assert created["type"] is FlowResultType.ABORT
    assert created["reason"] == "reauth_successful"
    assert second.data["token"]["id_token"]
    assert first.title == "First"

    started = await first.start_reauth_flow(hass)
    flow = _flow(hass, started["flow_id"])
    flow.flow_impl = _MockOAuth(user_id="third")
    flow.context["source"] = "unexpected"
    hass.data[DOMAIN][CONF_LW_OAUTH_USER_INPUT] = {
        "original_source": "nope",
        "entry_id": second.entry_id,
    }
    created = await flow.async_oauth_create_entry(
        {"token": {"id_token": _jwt({"sub": "third"})}}
    )
    assert created["type"] is FlowResultType.FORM
    assert created["step_id"] == "confirm_account_change"
    assert created["description_placeholders"]["current_account"] == "First"
    assert created["description_placeholders"]["new_account"] == "third"
    # oauth_create_entry was called on the flow object, so confirm the same way.
    created = await flow.async_step_confirm_account_change({})
    assert created["reason"] == "reconfigure_successful"
    assert first.unique_id == "third"
    assert second.unique_id == "second"


async def test_confirm_account_change_without_pending_aborts(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="kept",
        title="Kept",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "a"}},
    )
    entry.add_to_hass(hass)
    started = await entry.start_reauth_flow(hass)
    flow = _flow(hass, started["flow_id"])
    flow._pending_account_setup = None
    aborted = await flow.async_step_confirm_account_change({})
    assert aborted["type"] is FlowResultType.ABORT
    assert aborted["reason"] == "unknown"
    assert entry.unique_id == "kept"

    shown = await flow.async_step_confirm_account_change()
    assert shown["type"] is FlowResultType.FORM
    assert shown["step_id"] == "confirm_account_change"
    assert shown["description_placeholders"]["new_account"] == "unknown"

    flow._pending_account_setup = {"data": "bad", "title": None}
    shown = await flow.async_step_confirm_account_change()
    assert shown["description_placeholders"]["new_account"] == "unknown"

    taken = MockConfigEntry(
        domain=DOMAIN,
        unique_id="third",
        title="Taken",
        data={CONF_LW_AUTH_METHOD: "oauth", "token": {"access_token": "b"}},
    )
    taken.add_to_hass(hass)
    flow._pending_account_setup = {
        "data": {"token": {"id_token": _jwt({"sub": "third"})}},
        "title": None,
    }
    blocked = await flow.async_step_confirm_account_change({})
    assert blocked["type"] is FlowResultType.ABORT
    assert blocked["reason"] == "already_configured"
    assert entry.unique_id == "kept"

    flow._pending_account_setup = {
        "data": {"token": {"id_token": _jwt({"sub": "fresh"})}},
        "title": None,
    }
    applied = await flow.async_step_confirm_account_change({})
    assert applied["reason"] == "reauth_successful"
    assert entry.unique_id == "fresh"
    assert entry.title == "Kept"


async def test_finish_without_identity_and_repeat_hub(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, result["flow_id"])
    created = await flow._async_finish_account_setup({}, None)
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["title"] == "Lightwave Smart"
    assert flow.unique_id is None
    hass.config_entries.flow.async_abort(result["flow_id"])

    entry = MockConfigEntry(domain=DOMAIN, unique_id="hub@example.com", data={})
    entry.add_to_hass(hass)
    flow._discovered_cloud_id = None
    same = flow._data_with_discovered_hub({"keep": 1}, None)
    assert same == {"keep": 1}
    flow._remember_discovered_hub(entry)

    flow._discovered_cloud_id = "HUB1"
    merged = flow._data_with_discovered_hub({"keep": 1}, {CONF_LW_CLOUD_IDS: ["HUB1"]})
    assert merged[CONF_LW_CLOUD_IDS] == ["HUB1"]
    flow._remember_discovered_hub(entry)
    assert list(entry.data[CONF_LW_CLOUD_IDS]) == ["HUB1"]
    flow._remember_discovered_hub(entry)
    assert list(entry.data[CONF_LW_CLOUD_IDS]) == ["HUB1"]


_LIGHTWAVE_USER = "a01716f2-aba6-46b6-ba66-f57d0fc1a54a"


async def _connection_with_sub(self, username, password):
    return {CONF_ACCESS_TOKEN: _jwt({"sub": _LIGHTWAVE_USER}), "refresh_token": "r"}


async def test_password_unique_id_is_access_token_sub(hass, mock_link, mock_setup_entry):
    with patch(
        "custom_components.lightwave_smart.config_flow.lightwave_smartFlowHandler._test_connection",
        _connection_with_sub,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await _choose_method(hass, result["flow_id"], "refresh")
        result = await _submit_credentials(hass, result["flow_id"], username="User@Example.com")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == _LIGHTWAVE_USER
    assert result["data"][CONF_LW_USER_ID] == _LIGHTWAVE_USER
    assert result["data"][CONF_PASSWORD] is None


async def test_legacy_entry_keeps_saved_user_id(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "saved@example.com",
            CONF_LW_USER_ID: _LIGHTWAVE_USER,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert entry.unique_id == _LIGHTWAVE_USER


async def test_legacy_entry_uses_stored_access_token_sub(hass, mock_link, mock_setup_entry):
    from custom_components.lightwave_smart.utils import set_stored_tokens

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        title="Old house",
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "old@example.com",
            CONF_PASSWORD: "old",
        },
    )
    entry.add_to_hass(hass)
    await set_stored_tokens(
        hass, "old@example.com", {CONF_ACCESS_TOKEN: _jwt({"sub": _LIGHTWAVE_USER})}
    )
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert entry.unique_id == _LIGHTWAVE_USER


async def test_email_unique_id_upgrades_to_user_id(hass, mock_link, mock_setup_entry):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        title="House",
        data={
            CONF_LW_AUTH_METHOD: "password",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "old",
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.lightwave_smart.config_flow.lightwave_smartFlowHandler._test_connection",
        _connection_with_sub,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await _choose_method(hass, result["flow_id"], "password")
        result = await _submit_credentials(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id == _LIGHTWAVE_USER


def _upgraded_email_entry(hass, **data) -> MockConfigEntry:
    """An entry from before discovery, keyed by email because its token has no user id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        title="user@example.com",
        data={
            CONF_LW_AUTH_METHOD: "refresh",
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: None,
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_issue_86_upgrade_does_not_add_a_second_entry(hass, mock_link, mock_setup_entry):
    """An upgraded email entry hides discovery, and Account Linking asks before adding."""
    entry = _upgraded_email_entry(hass)
    ignored = MockConfigEntry(domain=DOMAIN, source=SOURCE_IGNORE, unique_id="discovery:OTHER")
    ignored.add_to_hass(hass)
    discovered = _discovery("Lightwave Link Plus 310BD2._lightwave._tcp.local.", "BC310BD2")
    hidden = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert hidden["type"] is FlowResultType.ABORT
    assert hidden["reason"] == "already_configured"

    impl = _MockOAuth(user_id=_LIGHTWAVE_USER)
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, impl)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _choose_method(hass, result["flow_id"], "oauth")
    result = await _finish_oauth(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_additional_account"
    assert result["description_placeholders"]["existing_accounts"] == "user@example.com"
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert hass.config_entries.async_entries(DOMAIN) == [entry, ignored]


async def test_discovery_uses_recorded_hub_serials(hass, mock_link, mock_setup_entry):
    _upgraded_email_entry(hass, **{CONF_LW_HUB_SERIALS: ["bc310bd2"]})
    discovered = _discovery("Lightwave Link Plus 310BD2._lightwave._tcp.local.", "BC310BD2")
    owned = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovered
    )
    assert owned["type"] is FlowResultType.ABORT
    assert owned["reason"] == "already_configured"

    other = _discovery("Lightwave Link Plus 999999._lightwave._tcp.local.", "BC999999")
    shown = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=other
    )
    assert shown["type"] is FlowResultType.FORM
    hass.config_entries.flow.async_abort(shown["flow_id"])

    suffix = _discovery("Lightwave Link Plus 310BD2._lightwave._tcp.local.", "310BD2")
    not_exact = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=suffix
    )
    assert not_exact["type"] is FlowResultType.FORM


async def test_confirm_additional_account_without_pending_aborts(hass, mock_link, mock_setup_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    flow = _flow(hass, result["flow_id"])
    aborted = await flow.async_step_confirm_additional_account()
    assert aborted["type"] is FlowResultType.ABORT
    assert aborted["reason"] == "unknown"
