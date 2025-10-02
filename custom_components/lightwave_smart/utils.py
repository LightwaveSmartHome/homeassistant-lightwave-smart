from .const import DOMAIN
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

def make_device_info(entity, name = None):
    device = entity._device
    product_code = device.product_code
    if device.virtual_product_code:
        product_code += "-" + device.virtual_product_code

    via_device = entity._lwlink.get_linkPlus_featureset_id(device.device_id)

    return DeviceInfo({
        "identifiers": { (DOMAIN, device.device_id) },
        "name": name or device.name + " Device",
        "manufacturer": device.manufacturer_code,
        "model": product_code,
        "serial_number": device.serial,
        "sw_version": device.firmware_version,
        "via_device": (DOMAIN, via_device),
    })
    
def make_entity_device_info(entity, name = None):
    via_device = entity._lwlink.get_linkPlus_featureset_id(entity._featureset_id)
    if entity._gen2 and not entity._device.is_hub():
        via_device = entity._device.device_id
        
    if entity._device.is_hub():
        # Keep original name, which is set in __init__.py
        name = None
        
    device_info = {
        "identifiers": { (DOMAIN, entity._featureset_id) },
        "manufacturer": entity._device.manufacturer_code,
        "model": entity._device.product_code,
        "via_device": (DOMAIN, via_device),
    }
    
    if name:
        device_info["name"] = name

    return DeviceInfo(device_info)

def get_extra_state_attributes(entity):
    """Return the optional state attributes."""
    feature_set = entity._lwlink.featuresets[entity._featureset_id]

    attribs = {}
    for featurename, feature in feature_set.features.items():
        attribs['lwrf_' + featurename] = feature.state
    return attribs

async def get_stored_tokens(hass: HomeAssistant, username: str) -> dict:
    store = storage.Store(hass, 1, f"{DOMAIN}_{username}_tokens")
    stored_tokens = await store.async_load()
    return stored_tokens if stored_tokens else {}

async def set_stored_tokens(hass: HomeAssistant, username: str, tokens: dict):
    store = storage.Store(hass, 1, f"{DOMAIN}_{username}_tokens")
    await store.async_save(tokens)
    