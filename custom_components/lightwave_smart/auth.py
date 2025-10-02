"""API for Lightwave Smart bound to HASS OAuth."""
import logging
from collections.abc import Iterable
from typing import cast
import asyncio

from homeassistant.components import cloud
from homeassistant.helpers import config_entry_oauth2_flow

from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation

from .auth_const import LW_API_SCOPES
from lightwave_smart import lightwave_smart


_LOGGER = logging.getLogger(__name__)
# _LOGGER.setLevel(logging.DEBUG)


def get_api_scopes(auth_implementation: str) -> Iterable[str]:
    """Return the Lightwave Smart API scopes based on the auth implementation."""

    if auth_implementation == cloud.DOMAIN:
        return set(
            {
                scope
                for scope in LW_API_SCOPES
            }
        )
    return sorted(LW_API_SCOPES)

class CustomOAuth2Session(config_entry_oauth2_flow.OAuth2Session):
    _lw_invalidate_token = False

    def __init__(self, hass, config_entry, implementation, auth_instance):
        super().__init__(hass, config_entry, implementation)
        self._auth_instance = auth_instance
    
    @property
    def valid_token(self) -> bool:
        if self._lw_invalidate_token:
            self._lw_invalidate_token = False
            return False
        
        return super().valid_token
    
class LightwaveSmartAuth(lightwave_smart.LWAuth):
    """Provide Lightwave Smart authentication tied to an OAuth2 based config entry."""

    def __init__(
        self,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize the auth."""
        super().__init__()

        # Create a custom session that wraps the original
        self._oauth_session = CustomOAuth2Session(
            oauth_session.hass,
            oauth_session.config_entry,
            oauth_session.implementation,
            self
        )

    def invalidate_access_token(self):
        """Clear the access token."""
        _LOGGER.debug(f"invalidate_access_token")
        
        self._oauth_session._lw_invalidate_token = True
        
        # make sure token is renewed
        asyncio.create_task(self._oauth_session.async_ensure_token_valid())
        
        # Retry not allowed - token update will trigger reload of config entry
        return False

    async def async_get_access_token(self) -> str:
        """Return a valid access token for Lightwave Smart API."""
        await self._oauth_session.async_ensure_token_valid()
        return cast(str, self._oauth_session.token["access_token"])
