import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, DOMAIN
from homeassistant.components.lock import LockEntity, LockEntityDescription, LockEntityFeature
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

LOCK = LockEntityDescription(
    key="lock",
    name="Lock",
    entity_category=EntityCategory.CONFIG
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave devices that are lockable."""

    locks = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    for featureset_id, name in link.get_with_feature("protection"):
        try:
            locks.append(LWRF2Lock(name, featureset_id, link, LOCK))
        except Exception as e: _LOGGER.exception("Could not add LWRF2Lock")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(locks)
    async_add_entities(locks)

class LWRF2Lock(LockEntity):
    """Representation of a LightwaveRF light."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, description):   
        _LOGGER.debug("Adding lock: %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = description

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._state = \
            self._featureset.features["protection"].state

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)


    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

    #TODO add async_will_remove_from_hass() to clean up

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        self._state = \
            self._featureset.features["protection"].state

    @property
    def is_locked(self):
        """Return the protection (lock) state."""
        return self._state == 1

    async def async_lock(self, **kwargs):
        """Turn the Lightwave lock on."""
        _LOGGER.debug("HA lock.lock received, kwargs: %s", kwargs)

        self._state = 1
        feature_id = self._featureset.features['protection'].id
        await self._lwlink.async_write_feature(feature_id, 1)

        self.async_schedule_update_ha_state()

    async def async_unlock(self, **kwargs):
        """Turn the Lightwave lock off"""
        _LOGGER.debug("HA lock.unlock received, kwargs: %s", kwargs)

        self._state = 0
        feature_id = self._featureset.features['protection'].id
        await self._lwlink.async_write_feature(feature_id, 0)

        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)