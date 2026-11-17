"""OAuth session and LightwaveSmartAuth."""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.helpers import config_entry_oauth2_flow

from custom_components.lightwave_smart.auth import (
    CustomOAuth2Session,
    LightwaveSmartAuth,
    get_api_scopes,
)
from custom_components.lightwave_smart.auth_const import LW_API_SCOPES
from custom_components.lightwave_smart.const import DOMAIN
from lightwave_smart.utils import LWConnectionException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.helpers.errors import oauth_reauth_error


def test_get_api_scopes():
    assert get_api_scopes("cloud") == set(LW_API_SCOPES)
    assert get_api_scopes("local") == sorted(LW_API_SCOPES)


class _Impl(config_entry_oauth2_flow.AbstractOAuth2Implementation):
    @property
    def name(self) -> str:
        return "Mock"

    @property
    def domain(self) -> str:
        return DOMAIN

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        return "https://example.com"

    async def async_resolve_external_data(self, external_data) -> dict:
        return {"access_token": "access", "expires_in": 3600}

    async def _async_refresh_token(self, token: dict) -> dict:
        return token


@pytest.fixture
def oauth_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": 9_999_999_999,
            },
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    return entry


async def test_token_invalidation_and_access_token(hass, oauth_entry):
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, _Impl())
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, oauth_entry)
    session = config_entry_oauth2_flow.OAuth2Session(hass, oauth_entry, implementation)
    auth = LightwaveSmartAuth(session)

    assert auth._oauth_session.valid_token is True
    assert auth.invalidate_access_token() is True
    assert auth.token_rejected is True
    assert auth._oauth_session.valid_token is False
    assert auth._oauth_session.valid_token is True

    auth._oauth_session.async_ensure_token_valid = AsyncMock()
    token = await auth.async_get_access_token()
    assert token == "access"
    assert auth.last_error is None
    assert auth.token_rejected is False

    auth._oauth_session.async_ensure_token_valid = AsyncMock(
        side_effect=oauth_reauth_error("denied")
    )
    with pytest.raises(LWConnectionException):
        await auth.async_get_access_token()
    assert auth.last_error is not None

    auth._oauth_session.async_ensure_token_valid = AsyncMock(side_effect=RuntimeError("down"))
    with pytest.raises(RuntimeError):
        await auth.async_get_access_token()
    assert isinstance(auth.last_error, RuntimeError)


async def test_custom_session_uses_parent_validity(hass, oauth_entry):
    implementation = _Impl()
    session = CustomOAuth2Session(hass, oauth_entry, implementation, object())
    with patch.object(
        config_entry_oauth2_flow.OAuth2Session,
        "valid_token",
        new_callable=PropertyMock,
        return_value=False,
    ):
        assert session.valid_token is False
