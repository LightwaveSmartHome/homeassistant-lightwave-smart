"""API for Lightwave Smart bound to HASS OAuth."""
import logging
from collections.abc import Iterable
from typing import cast

from homeassistant.helpers import config_entry_oauth2_flow

from .auth_const import LW_API_SCOPES
from lightwave_smart import lightwave_smart
from lightwave_smart.utils import LWConnectionException


_LOGGER = logging.getLogger(__name__)
# _LOGGER.setLevel(logging.DEBUG)

NABUCASA_SERVICE_DOMAIN = "lightwaverf"
_CLOUD_LINK_PROVIDER = "lightwave_smart_cloud_link"


async def _async_provide_cloud_link_implementation(
    hass, domain: str
) -> list[config_entry_oauth2_flow.AbstractOAuth2Implementation]:
    """Bridge Nabu Casa Account Linking ('lightwaverf') to 'lightwave_smart'."""
    if domain != "lightwave_smart" or "cloud" not in hass.data:
        return []
    try:
        from homeassistant.components.cloud import account_link
    except ImportError:
        return []
    return await account_link.async_provide_implementation(
        hass, NABUCASA_SERVICE_DOMAIN
    )


def async_register_cloud_account_link_provider(hass) -> None:
    """Ensure the 'lightwaverf' cloud account-link provider is registered."""
    config_entry_oauth2_flow.async_add_implementation_provider(
        hass, _CLOUD_LINK_PROVIDER, _async_provide_cloud_link_implementation
    )


def get_api_scopes(auth_implementation: str) -> Iterable[str]:
    """Return the Lightwave Smart API scopes based on the auth implementation."""

    if auth_implementation == "cloud":
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
        self.last_error: Exception | None = None
        self.token_rejected: bool = False

        # Create a custom session that wraps the original
        self._oauth_session = CustomOAuth2Session(
            oauth_session.hass,
            oauth_session.config_entry,
            oauth_session.implementation,
            self
        )

    def invalidate_access_token(self) -> bool:
        """Mark the cached token invalid so the caller retries with a refreshed token."""
        _LOGGER.debug("invalidate_access_token")
        self.token_rejected = True
        self._oauth_session._lw_invalidate_token = True
        return True

    async def async_get_access_token(self) -> str:
        """Return a valid access token for Lightwave Smart API."""
        self.token_rejected = False
        try:
            await self._oauth_session.async_ensure_token_valid()
        except config_entry_oauth2_flow.OAuth2TokenRequestReauthError as err:
            self.last_error = err
            raise LWConnectionException(str(err), retry=False) from err
        except Exception as err:
            self.last_error = err
            raise

        self.last_error = None
        return cast(str, self._oauth_session.token["access_token"])
