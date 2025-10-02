import logging
import voluptuous as vol
from typing import Any, Mapping
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, config_entry_oauth2_flow, selector
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow
)

from .const import (
    DOMAIN, 
    CONF_HOMEKIT, 
    CONF_AUTH_METHODS, 
    CONF_AUTH_METHOD, 
    CONF_API_KEY, 
    CONF_REFRESH_TOKEN, 
    CONF_ACCESS_TOKEN,
    CONF_INSTANCE_NAME
)
from .auth_const import LW_API_SCOPES
from .utils import set_stored_tokens
from .auth import get_api_scopes



_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AUTH_METHOD): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="oauth", label="OAuth2"),
                    selector.SelectOptionDict(value="refresh", label="Refresh"),
                    selector.SelectOptionDict(value="api_key", label="API_Key"),
                    selector.SelectOptionDict(value="password", label="Password"),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_AUTH_METHOD,
            )
        ),
    }
)
USER_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_INSTANCE_NAME): cv.string,
    }
)
API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): cv.string,
        vol.Required(CONF_REFRESH_TOKEN): cv.string,
    }
)
        
OAUTH_SCHEMA = vol.Schema(
    {
        # vol.Optional(CONF_INSTANCE_NAME): cv.string,
    }
)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


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
        
        
class lightwave_smartFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle authentication."""
    VERSION = 1
    MINOR_VERSION = 1
    
    DOMAIN = DOMAIN
    reauth_entry: ConfigEntry | None = None
    
    _user_input = None
    
    @property
    def logger(self):
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data that needs to be appended to the authorize url."""
        scopes = get_api_scopes(self.flow_impl.domain)
        return {"scope": " ".join(scopes)}

    async def _save_oauth_user_input(self, user_input: dict):
        """Save oauth_user_input to storage."""
        if self.hass is None or self.hass.data is None:
            return None
        
        if DOMAIN in self.hass.data and "oauth_user_input_save_used" in self.hass.data[DOMAIN] and self.hass.data[DOMAIN]["oauth_user_input_save_used"] == True:
            return None
            
        if DOMAIN not in self.hass.data:
            self.hass.data.setdefault(DOMAIN, {})
        
        user_input = self._set_user_input(user_input)
        self.hass.data[DOMAIN]["oauth_user_input"] = user_input
        return user_input

    def _get_oauth_user_input(self):
        """Get oauth_user_input from hass.data if available."""
        if self.hass is None or DOMAIN not in self.hass.data:
            return None
        
        oauth_user_input = self.hass.data[DOMAIN].get("oauth_user_input")
        if oauth_user_input is not None:
            self.hass.data[DOMAIN]["oauth_user_input"] = None
            self.hass.data[DOMAIN]["oauth_user_input_save_used"] = True
            _LOGGER.debug(f"_get_oauth_user_input - got oauth_user_input: {oauth_user_input}")
        
        return oauth_user_input


    def _get_auth_method_schema(self, user_input: dict | None = None) -> vol.Schema:
        schema = AUTH_SCHEMA

        if user_input is not None:
            if CONF_AUTH_METHOD in user_input and user_input[CONF_AUTH_METHOD] in CONF_AUTH_METHODS:
                if user_input[CONF_AUTH_METHOD] == "password" or user_input[CONF_AUTH_METHOD] == "refresh":
                    schema = USER_CREDENTIALS_SCHEMA
                
                elif user_input[CONF_AUTH_METHOD] == "api_key":
                    schema = API_KEY_SCHEMA
                
                elif user_input[CONF_AUTH_METHOD] == "oauth":
                    schema = OAUTH_SCHEMA
                
        return schema

    async def _evaluate_user_credentials(self, user_input: dict):
        _LOGGER.debug(f"_evaluate_user_credentials - user_input: {user_input}")
        
        base_code = None
        if CONF_PASSWORD in user_input and user_input[CONF_PASSWORD]:
            username = user_input[CONF_USERNAME]
            
            try:
                tokens = await self._test_connection(username, user_input[CONF_PASSWORD])
                await set_stored_tokens(hass=self.hass, username=username, tokens=tokens)
                
            except CannotConnect:
                base_code = "cannot_connect"
            except InvalidAuth:
                base_code = "invalid_auth"
            except Exception as e:
                base_code = f"unknown: {e}"
                
            else:
                return True

        return base_code

    def _evaluate_instance_name(self, user_input: dict):
        base_code = None
        if user_input is not None and CONF_INSTANCE_NAME in user_input and user_input[CONF_INSTANCE_NAME]:
            return True

        return base_code

    def _set_user_input(self, user_input):
        # combine user_input with self._user_input (previous user_input)
        if user_input is not None:
            if self._user_input is None:
                self._user_input = {}
            self._user_input = {**self._user_input, **user_input}
        return self._user_input

    async def _get_user_input(self, step_id, user_input: dict, data_schema: vol.Schema, base_code: str = None):
        _LOGGER.debug(f"_get_user_input - step_id: {step_id} - user_input: {user_input} - base_code: {base_code} - data_schema: {data_schema}")
        
        description_placeholders = {}
        # not sure if this is needed
        # if user_input is not None and CONF_USERNAME in user_input:
        #     description_placeholders = {
        #         "username": user_input[CONF_USERNAME]
        #     }
        
        return self.async_show_form(
            step_id=step_id,
            data_schema=data_schema,
            errors=base_code and {"base": base_code} or None,
            description_placeholders=description_placeholders
        )    


    async def _step_auth(self, step_id, user_input=None):
        _LOGGER.debug(f"_step_auth - step_id: {step_id} - user_input: {user_input}")
        
        last_user_input = user_input
        user_input = self._set_user_input(user_input)
        data_schema = self._get_auth_method_schema(user_input=user_input)
        
        _LOGGER.debug(f"_step_auth - data_schema: {data_schema} - user_input: {user_input}")
        
        base_code = None
        if data_schema is AUTH_SCHEMA:
            # this is the default step, always proceeds to further step
            step_id = "lightwave_auth_method"
            if user_input is not None:
                # if there is user input here then we know it is invalid (otherwise data_schema would be different)
                base_code = "invalid_auth_method"
        
        elif data_schema is USER_CREDENTIALS_SCHEMA:
            step_id = "user"
            base_code = await self._evaluate_user_credentials(user_input=user_input)
        
        elif data_schema is API_KEY_SCHEMA:
            # base_code = await self._evaluate_api_key_credentials(step_id=step_id, user_input=user_input)
            pass
        
        elif data_schema is OAUTH_SCHEMA:
            step_id = "oauth_auth_method"
            base_code = None
            
            
            _LOGGER.warning(f"?????????????????????? _step_auth - last_user_input: {last_user_input} - last_user_input.keys(): {last_user_input.keys()}")
            
            # or has no properties
            if not last_user_input.keys():
                base_code = True
            
            # we can't set the title when oauth, bypass for now
            # base_code = self._evaluate_instance_name(user_input=user_input)
        
        _LOGGER.debug(f"_step_auth - evaluated - step_id: {step_id} - base_code: {base_code}")
        
        if base_code is True:
            # user_input is valid for setup
            return True
        
        return await self._get_user_input(step_id=step_id, user_input=user_input, data_schema=data_schema, base_code=base_code)

    async def _get_existing_entry(self) -> ConfigEntry | None:
        entries = self._async_current_entries()
        existing_entry = None
        for entry in entries:
            existing_entry = entry
            _LOGGER.debug(f"_get_existing_entry - entry_id: {entry.entry_id} / unique_id: {entry.unique_id} / title: {entry.title} / version: {entry.version} / domain: {entry.domain} / state: {entry.state}")

        return existing_entry, entries

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle a flow start."""
        _LOGGER.debug(f"async_step_user - user_input: {user_input} - source: {self.source}")
        
        # Handle oauth_user_input if available - see async_step_oauth_auth_method - needed when oauth client credentials need to be set
        if self._user_input is None:
            oauth_user_input = self._get_oauth_user_input()
            if oauth_user_input is not None:
                self._user_input = oauth_user_input
                return await self.async_step_oauth_auth_method(user_input=oauth_user_input)
        

        existing_entry, entries = await self._get_existing_entry()
        if entries and len(entries) > 0:
            if self.source != SOURCE_REAUTH and self.source != SOURCE_RECONFIGURE:
                return self.async_abort(reason="single_instance_allowed")
            
        
        result = await self._step_auth(step_id="user", user_input=user_input)
        if result is True:
            user_input = self._set_user_input(user_input)
            if user_input[CONF_AUTH_METHOD] == "refresh":
                user_input[CONF_PASSWORD] = None
            
            # We dont use async_update_entry because we want to set the title
            # if existing_entry:
            #     self.hass.config_entries.async_update_entry(existing_entry, data=user_input)
            #     return self.async_abort(reason="reconfigure_successful")
            
            title = user_input[CONF_INSTANCE_NAME] if CONF_INSTANCE_NAME in user_input else None
            if title is None:
                title = user_input[CONF_USERNAME]
            
            # if existing entry, replace (must be SOURCE_RECONFIGURE, or SOURCE_REAUTH)
            unique_id = DOMAIN
            if existing_entry is not None:
                unique_id = existing_entry.unique_id
                
            _LOGGER.debug(f"async_step_user - Completed - user_input: {user_input} - unique_id: {unique_id} - title: {title} - existing_entry: {'Existing Entry Replaced' if existing_entry is not None else 'None'}")
            
            await self.async_set_unique_id(unique_id)
            return self.async_create_entry(title=title, data=user_input)

        return result
    
    async def async_step_lightwave_auth_method(self, user_input: dict | None = None) -> ConfigFlowResult:
        _LOGGER.debug(f"async_step_lightwave_auth_method - user_input: {user_input}")
        
        # # TODO - we will always have user input here, the result of the next call to _step_auth cannot be boolean
        # return await self._step_auth(step_id="lightwave_auth_method", user_input=user_input)
        
        result = await self._step_auth(step_id="lightwave_auth_method", user_input=user_input)
        if result is True:
            if user_input[CONF_AUTH_METHOD] == "oauth":
                await self._save_oauth_user_input(user_input=user_input)
            return await super().async_step_user()
        
        return result
    
    async def async_step_oauth_auth_method(self, user_input: dict | None = None) -> ConfigFlowResult:
        _LOGGER.debug(f"async_step_oauth_auth_method - user_input: {user_input}")
        
        if DOMAIN in self.hass.data and "oauth_user_input_save_used" in self.hass.data[DOMAIN]:
            self.hass.data[DOMAIN]["oauth_user_input_save_used"] = False
        
        result = await self._step_auth(step_id="oauth_auth_method", user_input=user_input)
        if result is True:
            # if auth client credentials are not set then the FlowHandler will be re-initialized after they are set
            # this is why we need to save the user_input
            user_input = self._set_user_input(user_input)
            result = await self._save_oauth_user_input(user_input=user_input)
            
            return await super().async_step_user()
        
        return result
    

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_user()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create an oauth config entry or update existing entry for reauth."""
        _LOGGER.debug(f"async_oauth_create_entry - data: {data}")
        
        
        # clear oauth_user_input by getting it (oauth_user_input_save_used)
        oauth_user_input = self._get_oauth_user_input()

        data = self._set_user_input(data)

        # existing_entry = await self.async_set_unique_id(DOMAIN)
        existing_entry, entries = await self._get_existing_entry()
        if existing_entry:
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            # await self.hass.config_entries.async_reload(existing_entry.entry_id)
            
            reason = "unknown"
            if self.source == SOURCE_REAUTH:
                reason = "reauth_successful"
            elif self.source == SOURCE_RECONFIGURE:
                reason = "reconfigure_successful"
                
            if reason == "unknown":
                _LOGGER.warning(f"async_oauth_create_entry - existing_entry - unknown reason - source: {self.source} - data: {data}")
                
            return self.async_abort(reason=reason)
        
        
        await self.async_set_unique_id(DOMAIN)
        return await super().async_oauth_create_entry(data)

    async def async_step_reconfigure(self, user_input: dict[str, any] | None = None):
        _LOGGER.debug(f"async_step_reconfigure - user_input: {user_input}")
        
        result = await self._step_auth(step_id="reconfigure", user_input=user_input)
        if result is True:
            self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=user_input
            )

        return result

    async def _test_connection(self, username: str, password: str) -> dict:
        """Test the connection to Lightwave."""
        from lightwave_smart import lightwave_smart
        
        link = None
        try:
            link = lightwave_smart.LWLink2()
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
                await link.async_deactivate("hass_test")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return lightwave_smartOptionsFlowHandler(config_entry)