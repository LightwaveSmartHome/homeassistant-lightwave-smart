import logging
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD)
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN, CONF_HOMEKIT, CONF_AUTH_METHOD, CONF_API_KEY, CONF_REFRESH_TOKEN, CONF_ACCESS_TOKEN
from .utils import set_stored_tokens

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

    async def _get_user_input(self, step_id, user_input: dict):
        base_code = None
        
        if user_input is not None:
            if not user_input[CONF_PASSWORD]:
                base_code = "invalid_auth"
            else:
                username = user_input[CONF_USERNAME]
                
                # Validate credentials before updating entry
                try:
                    tokens = await self._test_connection(username, user_input[CONF_PASSWORD])
                    # On successful connection, switch to refresh method
                    user_input = await self.set_user_data(user_input, tokens)
                    
                except CannotConnect:
                    base_code = "cannot_connect"
                except InvalidAuth:
                    base_code = "invalid_auth"
                except Exception as e:
                    base_code = f"unknown: {e}"
                    
                else:
                    await self.async_set_unique_id(username)
                    return True

            _LOGGER.error(f"_get_user_input: Error during step_id: {step_id} - {base_code}")

        return self.async_show_form(
            step_id=step_id,
            data_schema=USER_CONFIG_SCHEMA,
            errors=base_code and {"base": base_code} or None
        )    

    async def async_step_user(self, user_input=None):
        result = await self._get_user_input(step_id="user", user_input=user_input)
        if result is True:
            username = user_input[CONF_USERNAME]
            return self.async_create_entry(title=username, data=user_input)

        return result

    async def async_step_reconfigure(self, user_input: dict[str, any] | None = None):
        result = await self._get_user_input(step_id="reconfigure", user_input=user_input)
        if result is True:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=user_input
            )

        return result

    async def async_step_reauth(self, user_input=None):
        result = await self._get_user_input(step_id="reauth", user_input=user_input)
        if result is True:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates=user_input
            )
        return result

    async def _test_connection(self, username: str, password: str) -> dict:
        """Test the connection to Lightwave."""
        from lightwave_smart import lightwave_smart
        
        link = None
        try:
            link = lightwave_smart.LWLink2(auth_method="password")
            link.auth.set_auth_method(auth_method="password", username=username, password=password)
            
            connected = await link.async_activate(max_tries=3, force_keep_alive_secs=0, source="hass_test")
            if not connected:
                raise InvalidAuth("Invalid credentials")
                
            # Test getting hierarchy
            await link.async_get_hierarchy()
            
            # Get tokens
            tokens = link.auth.get_tokens()
            return tokens            
                
        except Exception as e:
            _LOGGER.error("Connection test failed: %s", e)
            if "auth" in str(e).lower() or "invalid" in str(e).lower() or "unauthorized" in str(e).lower():
                raise InvalidAuth("Invalid credentials") from e
            else:
                raise CannotConnect("Cannot connect to Lightwave service") from e
            
        finally:
            if link is not None:
                await link.async_deactivate()

    async def set_user_data(self, user_input: dict, tokens: dict):
        user_input[CONF_AUTH_METHOD] = "refresh"
        username = user_input[CONF_USERNAME]
        await set_stored_tokens(hass=self.hass, username=username, tokens=tokens)
        
        # Clear stored password
        user_input[CONF_PASSWORD] = None
        
        return user_input

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return lightwave_smartOptionsFlowHandler(config_entry)

class lightwave_smartOptionsFlowHandler(config_entries.OptionsFlow):

    def __init__(self, config_entry):
        pass

    async def async_step_init(self, user_input=None):
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            _LOGGER.debug("Received user input: %s ", user_input)
            return self.async_create_entry(title="", data=user_input)


        data = self.config_entry.data
            
        if self.config_entry.options:
            options = self.config_entry.options
            _LOGGER.debug(f"Creating options form using existing options: {options}")
        else:
            options = {
                CONF_HOMEKIT: False
            }
            _LOGGER.debug(f"Creating options form using default options: {options}")
            
        return self.async_show_form(
            step_id="user", 
            data_schema=vol.Schema({
                vol.Optional(CONF_HOMEKIT, default=options.get(CONF_HOMEKIT)): bool,
                vol.Remove(CONF_AUTH_METHOD): data.get(CONF_AUTH_METHOD, "unknown")
            })
        )