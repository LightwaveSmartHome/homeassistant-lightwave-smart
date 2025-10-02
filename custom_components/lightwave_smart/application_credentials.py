from homeassistant.core import HomeAssistant
from homeassistant.components.application_credentials import AuthorizationServer

LW_AUTH_SERVER = "https://auth.lightwaverf.com"

async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return authorization server."""
    return AuthorizationServer(
        authorize_url=f"{LW_AUTH_SERVER}/authorize",
        token_url=f"{LW_AUTH_SERVER}/token"
    )