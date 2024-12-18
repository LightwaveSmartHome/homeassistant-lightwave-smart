import logging
import voluptuous as vol

from .const import DOMAIN, CONF_PUBLICAPI, LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, \
    LIGHTWAVE_WEBHOOK, LIGHTWAVE_WEBHOOKID, LIGHTWAVE_LINKID, SERVICE_RECONNECT, SERVICE_WHDELETE, SERVICE_UPDATE
from homeassistant.config_entries import ConfigEntry    
from homeassistant.const import (CONF_USERNAME, CONF_PASSWORD)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

# Define supported platforms
PLATFORMS = ["switch", "light", "climate", "cover", "binary_sensor", "sensor", "lock", "event", "update"]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema({
            vol.Required(CONF_USERNAME): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
        })
    },
    extra=vol.ALLOW_EXTRA,
)

async def handle_webhook(hass, webhook_id, request):
    """Handle webhook callback."""
    for entry_id in hass.data[DOMAIN]:
        link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
        body = await request.json()
        _LOGGER.debug("Received webhook: %s ", body)
        link.process_webhook_received(body)
        for ent in hass.data[DOMAIN][entry_id][LIGHTWAVE_ENTITIES]:
            if ent.hass is not None:
                ent.async_schedule_update_ha_state(True)

def async_central_callback(**kwargs):
    _LOGGER.debug("Central callback")

async def async_setup(hass, config):

    async def service_handle_reconnect(call):
        _LOGGER.debug("Received service call reconnect")
        for entry_id in hass.data[DOMAIN]:
            link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
            try:
                # Close the existing WebSocket connection if it exists
                if link._ws and link._ws._websocket is not None:
                    await link._ws._websocket.close()
            except Exception as e:
                _LOGGER.error("Error closing WebSocket: %s", e)

    async def service_handle_update_states(call):
        _LOGGER.debug("Received service call update states")
        for entry_id in hass.data[DOMAIN]:
            link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
            await link.async_update_featureset_states()
            for ent in hass.data[DOMAIN][entry_id][LIGHTWAVE_ENTITIES]:
                if ent.hass is not None:
                    ent.async_schedule_update_ha_state(True)

    async def service_handle_delete_webhook(call):
        _LOGGER.debug("Received service call delete webhook")
        wh_name = call.data.get("webhookid")
        for entry_id in hass.data[DOMAIN]:
            link = hass.data[DOMAIN][entry_id][LIGHTWAVE_LINK2]
            await link.async_delete_webhook(wh_name)

    hass.services.async_register(DOMAIN, SERVICE_RECONNECT, service_handle_reconnect)
    hass.services.async_register(DOMAIN, SERVICE_WHDELETE, service_handle_delete_webhook)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE, service_handle_update_states)
    
    return True

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    from lightwave_smart import lightwave_smart

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(config_entry.entry_id, {})
    email = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]
    config_entry.add_update_listener(reload_lw)

    publicapi = config_entry.options.get(CONF_PUBLICAPI, False)
    if publicapi:
        _LOGGER.warning("Using Public API, this is experimental - if you have issues turn this off in the integration options")
        link = lightwave_smart.LWLink2Public(email, password)
    else:
        link = lightwave_smart.LWLink2(email, password)

    connected = await link.async_connect(max_tries=6, force_keep_alive_secs=0, source="hass")
    if not connected:
        return False
    await link.async_get_hierarchy()

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2] = link
    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES] = []
    if not publicapi:
        url = None
    else:
        webhook_id = hass.components.webhook.async_generate_id()
        hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_WEBHOOKID] = webhook_id
        _LOGGER.debug("Generated webhook: %s ", webhook_id)
        hass.components.webhook.async_register(
            'lightwave_smart', 'Lightwave webhook', webhook_id, handle_webhook)
        url = hass.components.webhook.async_generate_url(webhook_id)
        _LOGGER.debug("Webhook URL: %s ", url)
        await link.async_register_webhook_all(url, LIGHTWAVE_WEBHOOK, overwrite=True)

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_WEBHOOK] = url

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for featureset_id, hubname in link.get_hubs():
        device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            configuration_url="https://my.lightwaverf.com/a/login",
            entry_type=dr.DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, featureset_id)},
            manufacturer="Lightwave RF",
            name=hubname,
            model=link.featuresets[featureset_id].product_code
        )
        hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINKID] = featureset_id

    # Ensure every device associated with this config entry still exists
    # otherwise remove the device (and thus entities).
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        for identifier in device_entry.identifiers:
            _LOGGER.debug("Identifier found in Home Assistant device registry %s ", identifier[1])
            if identifier[1] in link.featuresets:
                _LOGGER.debug("Identifier exists in Lightwave config")
                break
        else:
            _LOGGER.debug("Identifier does not exist in Lightwave config, removing device")
            device_registry.async_remove_device(device_entry.id)
    for entity_entry in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        _LOGGER.debug("Entity registry item %s", entity_entry)
        _LOGGER.debug("Entity gen2 %s", entity_registry.async_get(entity_entry.entity_id))

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    return True

async def async_remove_entry(hass, config_entry):
    if LIGHTWAVE_WEBHOOK in hass.data[DOMAIN][config_entry.entry_id]:
        if hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_WEBHOOK] is not None:
            hass.components.webhook.async_unregister(hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_WEBHOOKID])

    for platform in PLATFORMS:
        await hass.config_entries.async_forward_entry_unload(config_entry, platform)

async def reload_lw(hass, config_entry):
    await async_remove_entry(hass, config_entry)
    await async_setup_entry(hass, config_entry)
