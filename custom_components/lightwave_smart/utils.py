from .const import DOMAIN
from homeassistant.helpers.device_registry import DeviceInfo

def make_device_info_V2(entity, name = None):
    device = entity.device
    product_code = device.product_code
    if device.virtual_product_code:
        product_code += "-" + device.virtual_product_code

    return DeviceInfo({
        "identifiers": { (DOMAIN, device.device_id) },
        "name": name or device.name,
        "manufacturer": device.manufacturer_code,
        "model": product_code,
        "serial_number": device.serial,
        "sw_version": device.firmware_version,
        "via_device": (DOMAIN, device.link),
    })
    
def make_device_info(entity, name = None):
    feature_set = entity._lwlink.featuresets[entity._featureset_id]

    product_code = feature_set.product_code
    if feature_set.virtual_product_code:
        product_code += "-" + feature_set.virtual_product_code

    return DeviceInfo({
        "identifiers": { (DOMAIN, entity._featureset_id) },
        "name": name or entity.name,
        "manufacturer": feature_set.manufacturer_code,
        "model": product_code,
        "serial_number": feature_set.serial,
        "sw_version": feature_set.firmware_version,
        "via_device": (DOMAIN, entity._linkid),
    })


def get_extra_state_attributes(entity):
    """Return the optional state attributes."""
    feature_set = entity._lwlink.featuresets[entity._featureset_id]

    attribs = {}
    for featurename, feature in feature_set.features.items():
        attribs['lwrf_' + featurename] = feature.state
    return attribs