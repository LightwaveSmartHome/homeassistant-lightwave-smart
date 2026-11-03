from .const import DOMAIN
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import storage

def _via_device_id(entity, identifier, own_identifier):
    """Return the registry id of an already registered parent device.

    via_device_id must be a device registry id. Omit it when the parent is
    missing, or when it is this device: passing None clears a link, and a
    self-link is rejected.
    """
    if not identifier or identifier == own_identifier:
        return None
    hass = getattr(entity._lwlink, "hass", None)
    config_entry_id = getattr(entity._lwlink, "config_entry_id", None)
    if hass is None or not config_entry_id:
        return None
    entry = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), config_entry_id
    )
    if entry is None:
        return None
    return entry.id

def make_device_info(entity, name = None):
    device = entity._device
    product_code = device.product_code
    if device.virtual_product_code:
        product_code += "-" + device.virtual_product_code

    via_identifier = entity._lwlink.get_linkPlus_featureset_id(device.device_id)

    device_info = {
        "identifiers": { (DOMAIN, device.device_id) },
        "name": name or device.name + " Device",
        "manufacturer": device.manufacturer_code,
        "model": product_code,
        "serial_number": device.serial,
        "sw_version": device.firmware_version,
    }
    via_device_id = _via_device_id(entity, via_identifier, device.device_id)
    if via_device_id:
        device_info["via_device_id"] = via_device_id

    return DeviceInfo(**device_info)
    
def make_entity_device_info(entity, name = None):
    via_identifier = entity._lwlink.get_linkPlus_featureset_id(entity._featureset_id)
    if entity._gen2 and not entity._device.is_hub():
        via_identifier = entity._device.device_id
        
    if entity._device.is_hub():
        # Keep original name, which is set in __init__.py
        name = None
        
    device_info = {
        "identifiers": { (DOMAIN, entity._featureset_id) },
        "manufacturer": entity._device.manufacturer_code,
        "model": entity._device.product_code,
    }
    
    if name:
        device_info["name"] = name

    via_device_id = _via_device_id(entity, via_identifier, entity._featureset_id)
    if via_device_id:
        device_info["via_device_id"] = via_device_id

    return DeviceInfo(**device_info)

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
    