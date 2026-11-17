"""Shared constructors for Home Assistant OAuth exceptions."""

from aiohttp import RequestInfo
from homeassistant.exceptions import OAuth2TokenRequestReauthError
from yarl import URL


def oauth_reauth_error(message: str = "denied"):
    """Build the keyword-only reauth error used by Home Assistant 2026.10."""
    info = RequestInfo(
        URL("https://auth.example/token"),
        "POST",
        {},
        URL("https://auth.example/token"),
    )
    return OAuth2TokenRequestReauthError(domain="lightwave_smart", request_info=info, message=message)
