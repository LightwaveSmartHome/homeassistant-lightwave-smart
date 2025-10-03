import logging
import voluptuous as vol
import asyncio

from .const import DOMAIN, LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, LIGHTWAVE_PLATFORMS, \
    SERVICE_RECONNECT, SERVICE_UPDATE, CONF_LW_AUTH_METHOD, CONF_API_KEY, \
    CONF_REFRESH_TOKEN, CONF_ACCESS_TOKEN, CONF_TOKEN_EXPIRY, SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS
from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD, CONF_TOKEN)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    config_validation as cv,
    config_entry_oauth2_flow,
    aiohttp_client,
)
from .utils import get_stored_tokens, set_stored_tokens

_LOGGER = logging.getLogger(__name__)

# Define supported platforms
PLATFORMS_FIRMWARE = ["update"]
PLATFORMS = ["switch", "light", "climate", "cover", "binary_sensor", "sensor", "lock", "event"]

def async_central_callback(**kwargs):
    _LOGGER.debug("Central callback")

async def async_setup(hass, config):
    
    async def service_handle_reconnect(call):
        _LOGGER.debug("Received service call reconnect")
        for entry_id in hass.data[DOMAIN]:
            link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
            try:
                await link.async_deactivate(source="service_handle_reconnect")
                await link.async_activate(source="service_handle_reconnect", connect_callback=link.async_get_hierarchy)
            except Exception as e:
                _LOGGER.error("Error deactivating Lightwave link: %s", e)

    async def service_handle_update_states(call):
        _LOGGER.debug("Received service call update states")
        for entry_id in hass.data[DOMAIN]:
            link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
            await link.async_update_featureset_states()
            for ent in hass.data[DOMAIN][entry_id][LIGHTWAVE_ENTITIES]:
                if ent.hass is not None:
                    ent.async_schedule_update_ha_state(True)

    async def service_handle_reset_enabled_status_to_defaults(call):
        """Reset enabled status to defaults."""
        _LOGGER.debug("reset_enabled_status_to_defaults: Received service call reset enabled status to defaults")
        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        
        count = 0
        enabled_count = 0
        for entry_id in hass.data.get(DOMAIN, {}):
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry:
                entities = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES]
                for entity_entry in er.async_entries_for_config_entry(entity_registry, entry_id):
                    count += 1
                    default_disabled = False
                    
                    entity_object = None
                    for ent in entities:
                        if hasattr(ent, 'unique_id') and ent.unique_id == entity_entry.unique_id:
                            entity_object = ent
                            break
                    
                    if entity_object is None:
                        _LOGGER.warning(f"reset_enabled_status_to_defaults: Could not find entity object for {entity_entry.entity_id}/{entity_entry.unique_id}")
                        continue
                        
                    default_disabled = not entity_object.entity_description.entity_registry_enabled_default
                    if entity_entry.disabled == default_disabled:
                        continue
                    
                    _LOGGER.info(f"reset_enabled_status_to_defaults: Entity {entity_entry.entity_id}/{entity_entry.unique_id} will be changed to disabled: {default_disabled}")
                    
                    entity_registry.async_update_entity(
                        entity_entry.entity_id, 
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION if default_disabled else None
                    )
                    enabled_count += 1
        
        _LOGGER.info(f"reset_enabled_status_to_defaults: Entities have been reset to defaults: {enabled_count} of {count}")

    hass.services.async_register(DOMAIN, SERVICE_RECONNECT, service_handle_reconnect)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE, service_handle_update_states)
    hass.services.async_register(DOMAIN, SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS, service_handle_reset_enabled_status_to_defaults)
    
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {})
    
    
    config_entry.async_on_unload(config_entry.add_update_listener(reload_lw))
    
    link = await setup_link_lw(hass, config_entry)
    try:
        connected = await link.async_activate(source="hass", connect_callback=link.async_get_hierarchy)
        if not connected:
            raise ConfigEntryAuthFailed("Failed to connect to Lightwave service. Please check your credentials.")
    except Exception as e:
        _LOGGER.error(f"Error connecting to Lightwave: {e}")
        raise ConfigEntryAuthFailed(f"Authentication failed: {str(e)}")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2] = link
    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES] = []
    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_PLATFORMS] = []

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for featureset_id, hubname in link.get_hubs():
        structure_name = link.get_structure_name(featureset_id)
        if structure_name is not None:
            hubname = f"{hubname} {structure_name}"
        
        device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            configuration_url="https://my.lightwaverf.com/a/login",
            entry_type=dr.DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, featureset_id)},
            manufacturer="Lightwave",
            name=hubname,
            model=link.featuresets[featureset_id].product_code
        )

    remove_missing_devices_and_entities(config_entry, link, device_registry, entity_registry)
    
    try:
        await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS_FIRMWARE)
        hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_PLATFORMS].extend(PLATFORMS_FIRMWARE)
    except Exception as e:
        _LOGGER.warning("No firmware platforms loaded: %s", e)
    
    try:
        await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
        hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_PLATFORMS].extend(PLATFORMS)
    except Exception as e:
        _LOGGER.warning("Some main platforms not loaded: %s", e)
    
    return True

async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry, source: str = "unload") -> bool:
    """Unload a config entry."""
    _LOGGER.info(f"Unloading config entry: '{config_entry.entry_id}' from {source}")
    
    # Check if the config entry was actually loaded
    if config_entry.entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.warning(f"Config entry '{config_entry.entry_id}' was never loaded, skipping unload")
        return True
    
    # await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS_FIRMWARE)
    # await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
        
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    
    loaded_platforms = entry_data[LIGHTWAVE_PLATFORMS]
    if loaded_platforms:
        try:
            await hass.config_entries.async_unload_platforms(config_entry, loaded_platforms)
        except Exception as e:
            _LOGGER.warning(f"Error unloading platforms for config entry '{config_entry.entry_id}': {e}")
    else:
        _LOGGER.warning(f"No platforms were loaded for config entry '{config_entry.entry_id}'")
    
    # Clean up connection to Lightwave backend
    if LIGHTWAVE_LINK2 in entry_data:
        link = entry_data[LIGHTWAVE_LINK2]
        try:
            await link.async_deactivate("async_unload_entry")
            _LOGGER.debug("Deactivated Lightwave link")
        except Exception as e:
            _LOGGER.error(f"Error deactivating Lightwave link: {e}")
    
    # Remove entry data from hass.data if still there
    if config_entry.entry_id in hass.data[DOMAIN]:
        del hass.data[DOMAIN][config_entry.entry_id]
    
    _LOGGER.info(f"Successfully unloaded config entry: '{config_entry.entry_id}' - un-loaded_platforms: {len(loaded_platforms)}")
    return True

async def async_remove_entry(hass, config_entry):
    """Remove a config entry - this is called when the integration is removed."""
    _LOGGER.debug(f"Removing config entry: {config_entry.entry_id}")
    pass

async def reload_lw(hass, config_entry):
    """Reload the config entry (called when System Options changed)."""
    _LOGGER.info(f"Reloading config entry: '{config_entry.entry_id}'")
    await async_unload_entry(hass=hass, config_entry=config_entry, source="reload")
    await async_setup_entry(hass=hass, config_entry=config_entry)

async def setup_link_lw(hass, config_entry):
    from lightwave_smart import lightwave_smart
    
    auth_method = CONF_LW_AUTH_METHOD in config_entry.data and config_entry.data[CONF_LW_AUTH_METHOD] or 'password'
    username = CONF_USERNAME in config_entry.data and config_entry.data[CONF_USERNAME] or None
    password = CONF_PASSWORD in config_entry.data and config_entry.data[CONF_PASSWORD] or None
    
    tokens = await get_stored_tokens(hass, username)
    api_key = tokens.get(CONF_API_KEY)
    access_token = tokens.get(CONF_ACCESS_TOKEN)
    refresh_token = tokens.get(CONF_REFRESH_TOKEN)
    token_expiry = tokens.get(CONF_TOKEN_EXPIRY)
    
    log_data = {
        "auth_method": auth_method,
        "username": username,
        "password": "yes" if password else "no",
        "api_key": api_key[:1] + "..." + api_key[-1:] if api_key else "no",
        "access_token": access_token[:5] + "..." + access_token[-5:] if access_token else "no",
        "refresh_token": refresh_token[:1] + "..." + refresh_token[-1:] if refresh_token else "no",
        "token_expiry": token_expiry if token_expiry else "no"
    }
    _LOGGER.info(f"Setting up Lightwave link, config entry data: {log_data}")
    
    if auth_method == "oauth":
        from .auth import LightwaveSmartAuth
        
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, config_entry
            )
        )
        session = config_entry_oauth2_flow.OAuth2Session(hass, config_entry, implementation)
        
        lightwaveSmartAuth = LightwaveSmartAuth(session)
        link = lightwave_smart.LWLink2(auth=lightwaveSmartAuth)
        
    else:
        def on_token_refresh(access_token, refresh_token, token_expiry):
            _LOGGER.info("Updating tokens in storage")
            asyncio.create_task(
                set_stored_tokens(hass=hass, username=username, tokens={ 
                    CONF_ACCESS_TOKEN: access_token, 
                    CONF_REFRESH_TOKEN: refresh_token, 
                    CONF_TOKEN_EXPIRY: token_expiry 
                })
            )
            
        link = lightwave_smart.LWLink2()
        link.auth.set_token_refresh_callback(on_token_refresh)
        
        if auth_method == "password":
            link.auth.set_auth_method(auth_method=auth_method, username=username, password=password, access_token=access_token, refresh_token=refresh_token, token_expiry=token_expiry)
        else:
            link.auth.set_auth_method(auth_method=auth_method, api_key=api_key, access_token=access_token, refresh_token=refresh_token, token_expiry=token_expiry)

    return link

def remove_missing_devices_and_entities(config_entry, link, device_registry, entity_registry):
    # Ensure every device associated with this config entry still exists
    # otherwise remove the device (and thus entities).
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        for identifier in device_entry.identifiers:
            _LOGGER.debug(f"Identifier found in Home Assistant device registry: {identifier[1]} for device: {device_entry.id}")
            if identifier[1] in link.featuresets:
                _LOGGER.debug(f"Identifier exists in Lightwave config for device: {device_entry.id}")
                break
        else:
            _LOGGER.debug(f"Identifier does not exist in Lightwave config, removing device: {device_entry.id}")
            device_registry.async_remove_device(device_entry.id)
    
    # if in debug mode, log the entity registry
    if _LOGGER.isEnabledFor(logging.DEBUG):
        for entity_entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        ):
            _LOGGER.debug(f"Entity registry item: {entity_entry}")
            _LOGGER.debug(f"Entity: {entity_registry.async_get(entity_entry.entity_id)}")
