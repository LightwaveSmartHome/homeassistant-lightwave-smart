import logging
import asyncio
import voluptuous as vol
from typing import Any, Mapping
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, config_entry_oauth2_flow, selector
from homeassistant.loader import async_get_integration
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.helpers import storage
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    DOMAIN, 
    CONF_HOMEKIT, 
    CONF_LW_AUTH_METHODS, 
    CONF_LW_AUTH_METHOD, 
    CONF_API_KEY, 
    CONF_REFRESH_TOKEN, 
    CONF_ACCESS_TOKEN,
    CONF_LW_INSTANCE_NAME,
    CONF_LW_OAUTH_USER_INPUT
)
from .auth_const import LW_API_SCOPES
from .utils import set_stored_tokens
from .auth import async_register_cloud_account_link_provider, get_api_scopes



_LOGGER = logging.getLogger(__name__)

_ZEROCONF_SERVICE_TYPE = "_lightwave._tcp.local."
_TRANSIENT_FLOW_KEYS = ("original_source", "application_credentials")


def _strip_flow_keys(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of flow data without internal flow bookkeeping keys."""
    return {k: v for k, v in data.items() if k not in _TRANSIENT_FLOW_KEYS}


def _zeroconf_hub_name(discovery_info: ZeroconfServiceInfo) -> str:
    """Return the advertised hub name without the service type suffix."""
    name = (discovery_info.name or "").strip().rstrip(".")
    service_type = (discovery_info.type or _ZEROCONF_SERVICE_TYPE).strip(".")
    suffix = f".{service_type}" if service_type else ""
    if suffix and name.endswith(suffix):
        name = name[: -len(suffix)]
    return name or "Lightwave Link Plus"


def _zeroconf_cloud_id(discovery_info: ZeroconfServiceInfo) -> str | None:
    """Return the Link Plus cloud number from the mDNS txt record."""
    raw = (discovery_info.properties or {}).get("id")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if raw is None:
        return None
    cloud_id = str(raw).strip()
    return cloud_id or None


def _zeroconf_flow_title(discovery_info: ZeroconfServiceInfo) -> str:
    """Return the discovery title with the cloud number shown once.

    The mDNS name ends with a short form of the cloud number, for example
    "Lightwave Link Plus 310BD2" when the cloud number is BC310BD2.
    """
    name = _zeroconf_hub_name(discovery_info)
    cloud_id = _zeroconf_cloud_id(discovery_info)
    if not cloud_id:
        return name
    base, _, short_id = name.rpartition(" ")
    if short_id and (short_id == cloud_id or cloud_id.endswith(short_id)):
        name = base
    if not name or name == cloud_id:
        return cloud_id
    return f"{name} ({cloud_id})"


AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LW_AUTH_METHOD, default="oauth"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="oauth", label="OAuth2"),
                    selector.SelectOptionDict(value="refresh", label="Refresh"),
                    # TODO - implement API_Key 
                    # selector.SelectOptionDict(value="api_key", label="API_Key"),
                    selector.SelectOptionDict(value="password", label="Password"),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key=CONF_LW_AUTH_METHOD,
            )
        ),
    }
)
USER_CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_LW_INSTANCE_NAME): cv.string,
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
        # vol.Optional(CONF_LW_INSTANCE_NAME): cv.string,
    }
)

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class lightwave_smartOptionsFlowHandler(OptionsFlowWithReload):

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
                vol.Remove(CONF_LW_AUTH_METHOD): data.get(CONF_LW_AUTH_METHOD, "unknown")
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
    _discovered_cloud_id: str | None = None
    
    @property
    def logger(self):
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict:
        """Extra data that needs to be appended to the authorize url."""
        scopes = get_api_scopes(self.flow_impl.domain)
        return {"scope": " ".join(scopes)}

    async def _get_application_credential(self, optimistic=False):
        # due to HA caching we may not immediately be able to read the application_credentials
        # add a delay to allow for the data/cache to be updated (is there a better way?)
        store = storage.Store(self.hass, version=1, key="application_credentials", private=True)
        
        if not optimistic:
            await asyncio.sleep(10)
            
        stored_credentials = await store.async_load()
        if stored_credentials and 'items' in stored_credentials:
            for item in stored_credentials['items']:
                if item.get('domain') == self.DOMAIN:
                    _LOGGER.debug(f"_get_application_credential - application_credential: {item} - optimistic: {optimistic}")
                    return item
        return None

    async def _save_oauth_user_input(self, user_input: dict):
        """Save oauth_user_input to storage."""
        _LOGGER.debug(f"_save_oauth_user_input - user_input: {user_input}")
        
        if self.hass is None or self.hass.data is None:
            _LOGGER.warning(f"_save_oauth_user_input - not saved - hass/hass.data is None - user_input: {user_input}")
            return None
        
        if DOMAIN not in self.hass.data:
            _LOGGER.debug(f"_save_oauth_user_input - hass.data does not have DOMAIN - user_input: {user_input}")
            self.hass.data.setdefault(DOMAIN, {})
        
        user_input = self._set_user_input(user_input)
        self.hass.data[DOMAIN].update({CONF_LW_OAUTH_USER_INPUT: user_input})
        return user_input

    def _pop_oauth_user_input(self):
        """Pop oauth_user_input from hass.data if available."""
        if DOMAIN not in self.hass.data:
            _LOGGER.debug(f"_pop_oauth_user_input - hass.data does not have DOMAIN - setting default")
            self.hass.data.setdefault(DOMAIN, {})
        
        oauth_user_input = self.hass.data[DOMAIN].pop(CONF_LW_OAUTH_USER_INPUT, None)
        _LOGGER.debug(f"_pop_oauth_user_input - oauth_user_input: {oauth_user_input}")
        
        return oauth_user_input


    def _get_auth_method_schema(self, user_input: dict | None = None) -> vol.Schema:
        schema = AUTH_SCHEMA

        if user_input is not None:
            if CONF_LW_AUTH_METHOD in user_input and user_input[CONF_LW_AUTH_METHOD] in CONF_LW_AUTH_METHODS:
                if user_input[CONF_LW_AUTH_METHOD] == "password" or user_input[CONF_LW_AUTH_METHOD] == "refresh":
                    schema = USER_CREDENTIALS_SCHEMA
                
                elif user_input[CONF_LW_AUTH_METHOD] == "api_key":
                    schema = API_KEY_SCHEMA
                
                elif user_input[CONF_LW_AUTH_METHOD] == "oauth":
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
        if user_input is not None and CONF_LW_INSTANCE_NAME in user_input and user_input[CONF_LW_INSTANCE_NAME]:
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
        
        discovered_hub = ""
        if self._discovered_cloud_id:
            discovered_hub = f"Cloud number: {self._discovered_cloud_id}\n\n"
        return self.async_show_form(
            step_id=step_id,
            data_schema=data_schema,
            errors=base_code and {"base": base_code} or None,
            description_placeholders={"discovered_hub": discovered_hub},
        )    


    async def _step_auth(self, step_id, user_input=None):
        _LOGGER.debug(f"_step_auth - invoking step_id: {step_id} - user_input: {user_input}")
        
        last_user_input = user_input
        user_input = self._set_user_input(user_input)
        data_schema = self._get_auth_method_schema(user_input=user_input)
        
        _LOGGER.debug(f"_step_auth - data_schema: {data_schema} - user_input: {user_input}")
        
        base_code = None
        if data_schema is AUTH_SCHEMA:
            # this is the default step, always proceeds to further step
            step_id = CONF_LW_AUTH_METHOD      # "lightwave_auth_method"
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
            async_register_cloud_account_link_provider(self.hass)
            implementations = await config_entry_oauth2_flow.async_get_implementations(
                self.hass, self.DOMAIN
            )
            if implementations:
                return await self.async_step_oauth_auth_method(user_input=user_input)
            step_id = "oauth_auth_method"
            base_code = None
        
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


    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.debug("async_step_zeroconf: %s", discovery_info)

        # Check if already configured. A second hub uses the same unique id,
        # so another discovery flow aborts while this one is open.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        self._discovered_cloud_id = _zeroconf_cloud_id(discovery_info)
        self.context["title_placeholders"] = {"name": _zeroconf_flow_title(discovery_info)}

        return await self.async_step_user()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle a flow start."""
        _LOGGER.debug(f"async_step_user - user_input: {user_input} - source: {self.source}")

        # Handle oauth_user_input if available - see async_step_oauth_auth_method - needed when oauth client credentials need to be set first
        if self._user_input is None:
            # if no _user_input then this is a new instance of FlowHandler, check for oauth_user_input
            oauth_user_input = self._pop_oauth_user_input()
            if oauth_user_input is not None:
                application_credentials = await self._get_application_credential()
                if application_credentials is not None:
                    oauth_user_input["application_credentials"] = True
                    return await self.async_step_oauth_auth_method(user_input=oauth_user_input)
        

        existing_entry, entries = await self._get_existing_entry()
        if entries and len(entries) > 0:
            if self.source != SOURCE_REAUTH and self.source != SOURCE_RECONFIGURE:
                return self.async_abort(reason="single_instance_allowed")
        
        
        result = await self._step_auth(step_id="user", user_input=user_input)
        if result is True:
            user_input = _strip_flow_keys(self._set_user_input(user_input))
            
            # if refresh then clear the password
            if user_input[CONF_LW_AUTH_METHOD] == "refresh":
                user_input[CONF_PASSWORD] = None
            
            title = user_input.get(CONF_LW_INSTANCE_NAME) or user_input.get(CONF_USERNAME)
            
            if existing_entry is not None:
                if not title:
                    title = existing_entry.title
                _LOGGER.debug(
                    "async_step_user - Completed update - entry_id: %s - source: %s - title: %s",
                    existing_entry.entry_id,
                    self.source,
                    title,
                )
                return self.async_update_reload_and_abort(
                    existing_entry,
                    title=title,
                    data=user_input,
                )
                
            _LOGGER.debug(
                "async_step_user - Completed create - unique_id: %s - title: %s",
                DOMAIN,
                title,
            )
            
            # A discovered hub keeps a flow open with this unique id. Allow this
            # setup to finish, and Home Assistant clears that waiting flow.
            await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
            return self.async_create_entry(title=title, data=user_input)

        return result
    
    async def async_step_lightwave_auth_method(self, user_input: dict | None = None) -> ConfigFlowResult:
        _LOGGER.debug(f"async_step_lightwave_auth_method - user_input: {user_input}")
        return await self.async_step_user(user_input=user_input)
    
    async def async_step_oauth_auth_method(self, user_input: dict | None = None) -> ConfigFlowResult:
        _LOGGER.debug(f"async_step_oauth_auth_method - user_input: {user_input}")
        async_register_cloud_account_link_provider(self.hass)
        
        if user_input is not None:
            # oauth_user_input is used to tell us to continue the oauth flow after credentials are added
            # once we have credentials we use it to store the original source for use in async_oauth_create_entry
            
            implementations = await config_entry_oauth2_flow.async_get_implementations(
                self.hass, self.DOMAIN
            )
            has_credentials = bool(implementations) or ("application_credentials" in user_input)
            if not has_credentials:
                has_credentials = bool(await self._get_application_credential(optimistic=True))

            if not has_credentials:
                user_input = self._set_user_input(user_input)
                await self._save_oauth_user_input(user_input=user_input)
            else:
                await self._save_oauth_user_input(user_input={"original_source": self.source})
                
            return await super().async_step_user()
        
        _LOGGER.warning(f"async_step_oauth_auth_method - user_input is None")
        
        return await self.async_step_user()
    

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        _LOGGER.info(f"async_step_reauth - entry_data: {entry_data}")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_user()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create an oauth config entry or update existing entry for reauth/reconfigure."""
        _LOGGER.debug("async_oauth_create_entry - keys: %s", sorted(data.keys()))
        
        source = self.source
        
        # TODO - title is set from Oauth name, we could set the title too directly using storage class
        
        # when Oauth client credentials are needed to be set, the extra step means we don't know
        # the original source, so we need to store it in the oauth_user_input
        oauth_user_input = self._pop_oauth_user_input()
        if oauth_user_input is not None:
            if "original_source" in oauth_user_input:
                source = oauth_user_input["original_source"]

        data = _strip_flow_keys(self._set_user_input(data))
        data[CONF_LW_AUTH_METHOD] = "oauth"

        existing_entry, entries = await self._get_existing_entry()
        if existing_entry:
            title = data.get(CONF_LW_INSTANCE_NAME) or existing_entry.title
            if source == SOURCE_REAUTH:
                reason = "reauth_successful"
            elif source == SOURCE_RECONFIGURE:
                reason = "reconfigure_successful"
            else:
                _LOGGER.warning(
                    "async_oauth_create_entry - existing_entry - unexpected source: %s",
                    source,
                )
                reason = "reconfigure_successful"
                
            return self.async_update_reload_and_abort(
                existing_entry,
                title=title,
                data=data,
                reason=reason,
            )
        
        
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        title = (
            data.get(CONF_LW_INSTANCE_NAME)
            or ("Lightwave Smart" if self.flow_impl.domain == "cloud" else self.flow_impl.name)
        )
        return self.async_create_entry(title=title, data=data)

    async def async_step_reconfigure(self, user_input: dict[str, any] | None = None):
        _LOGGER.debug(f"async_step_reconfigure - user_input: {user_input}")
        # self._abort_if_unique_id_mismatch(reason="unique_id_mismatch")
        return await self.async_step_user(user_input=user_input)

    async def _test_connection(self, username: str, password: str) -> dict:
        """Test the connection to Lightwave."""
        from lightwave_smart import lightwave_smart
        
        link = None
        try:
            integration = await async_get_integration(self.hass, DOMAIN)
            link = lightwave_smart.LWLink2(client_version=integration.version)
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
        return lightwave_smartOptionsFlowHandler()