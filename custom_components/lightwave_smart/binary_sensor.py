import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, CONF_HOMEKIT, DOMAIN
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
# Device Classes
try:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    DEVICE_CLASS_WINDOW = BinarySensorDeviceClass.WINDOW
    DEVICE_CLASS_PLUG = BinarySensorDeviceClass.PLUG
    DEVICE_CLASS_MOTION = BinarySensorDeviceClass.MOTION
except ImportError:
    from homeassistant.components.binary_sensor import (DEVICE_CLASS_WINDOW, DEVICE_CLASS_PLUG, DEVICE_CLASS_MOTION)
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers import entity_registry as er
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

SENSORS = [
    BinarySensorEntityDescription(
        key="windowPosition",
        device_class=DEVICE_CLASS_WINDOW,
        name="Window Position",
    ),
    BinarySensorEntityDescription(
        key="outletInUse",
        device_class=DEVICE_CLASS_PLUG,
        name="Socket In Use",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="movement",
        device_class=DEVICE_CLASS_MOTION,
        name="Movement",
    ),
    BinarySensorEntityDescription(
        key="uiDigitalInput",
        name="DigitalInput",
    )
]

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave sensors."""

    sensors = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    homekit = config_entry.options.get(CONF_HOMEKIT, False)

    for featureset_id, featureset in link.featuresets.items():
        for description in SENSORS:
            if featureset.has_feature(description.key):
                try:
                    sensors.append(LWRF2BinarySensor(featureset.name, featureset_id, link, description, homekit))
                except Exception as e: _LOGGER.exception("Could not add LWRF2BinarySensor")
    
    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(sensors)
    async_add_entities(sensors)

class LWRF2BinarySensor(BinarySensorEntity):
    """Representation of a LightwaveRF window sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, description, homekit):
        _LOGGER.debug("Adding binary sensor %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = description
        
        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._homekit = homekit

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_name = self.entity_description.name

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        
        self._attr_device_info = make_entity_device_info(self, name)
        
        self._state = \
            self._featureset.features[self.entity_description.key].state


    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        if self._homekit:
            if entity_entry is not None and not entity_entry.hidden:
                registry.async_update_entity(
                    self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        else:
            if entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(self.entity_id, hidden_by=None)
                
    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        self._state = \
            self._featureset.features[self.entity_description.key].state

    @property
    def is_on(self):
        """Lightwave switch is on state."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)
