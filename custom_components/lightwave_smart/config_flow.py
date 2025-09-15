import logging
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD)
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN, CONF_PUBLICAPI, CONF_HOMEKIT
import voluptuous as vol
_LOGGER = logging.getLogger(__name__)

USER_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

class lightwave_smartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    async def async_step_user(self, user_input=None):
        self._errors = {}

        if user_input is not None:
            # Validate credentials before creating entry
            try:
                await self._test_connection(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except CannotConnect:
                self._errors["base"] = "cannot_connect"
            except InvalidAuth:
                self._errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                self._errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                return self.async_create_entry(title=user_input[CONF_USERNAME], data=user_input)

        return self.async_show_form(
            step_id='user',
            data_schema=USER_CONFIG_SCHEMA,
            errors=self._errors
        )

    async def async_step_reconfigure(self, user_input: dict[str, any] | None = None):
        if user_input is not None:
            # Validate credentials before updating entry
            try:
                await self._test_connection(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except CannotConnect:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=USER_CONFIG_SCHEMA,
                    errors={"base": "cannot_connect"}
                )
            except InvalidAuth:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=USER_CONFIG_SCHEMA,
                    errors={"base": "invalid_auth"}
                )
            except Exception:
                _LOGGER.exception("Unexpected exception during reconfigure")
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=USER_CONFIG_SCHEMA,
                    errors={"base": "unknown"}
                )
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=USER_CONFIG_SCHEMA
        )

    async def _test_connection(self, username: str, password: str) -> None:
        """Test the connection to Lightwave."""
        try:
            from lightwave_smart import lightwave_smart
            
            # Test with regular API first
            link = lightwave_smart.LWLink2(username, password)
            connected = await link.async_connect(max_tries=3, force_keep_alive_secs=0, source="hass_test")
            
            if not connected:
                raise InvalidAuth("Invalid credentials")
                
            # Test getting hierarchy
            await link.async_get_hierarchy()
            
            # Close the test connection
            if hasattr(link, '_ws') and link._ws and link._ws._websocket is not None:
                await link._ws._websocket.close()
                
        except Exception as e:
            _LOGGER.error("Connection test failed: %s", e)
            if "auth" in str(e).lower() or "invalid" in str(e).lower() or "unauthorized" in str(e).lower():
                raise InvalidAuth("Invalid credentials") from e
            else:
                raise CannotConnect("Cannot connect to Lightwave service") from e

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return lightwave_smartOptionsFlowHandler(config_entry)

class lightwave_smartOptionsFlowHandler(config_entries.OptionsFlow):

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            _LOGGER.debug("Received user input: %s ", user_input)
            return self.async_create_entry(title="", data=user_input)

        if self.config_entry.options:
            options = self.config_entry.options
            _LOGGER.debug("Creating options form using existing options: %s ", options)
        else:
            options = {
                CONF_PUBLICAPI: False,
                CONF_HOMEKIT: False
            }
            _LOGGER.debug("Creating options form using default options")

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({
                vol.Optional(CONF_PUBLICAPI, default=options.get(CONF_PUBLICAPI)): bool,
                vol.Optional(CONF_HOMEKIT, default=options.get(CONF_HOMEKIT)): bool
            })
        )