import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, DOMAIN
from homeassistant.components.cover import CoverEntity, CoverEntityDescription, CoverDeviceClass
try:
    from homeassistant.components.cover import CoverEntityFeature
    SUPPORT_CLOSE = CoverEntityFeature.CLOSE
    SUPPORT_OPEN = CoverEntityFeature.OPEN
    SUPPORT_STOP = CoverEntityFeature.STOP
except ImportError:
    from homeassistant.components.cover import (
        SUPPORT_CLOSE, SUPPORT_OPEN,
        SUPPORT_STOP)
from homeassistant.core import callback
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

COVER = CoverEntityDescription(
    key="cover",
    name="Cover",
    # translation_key="curtain",
    # device_class=CoverDeviceClass.CURTAIN,    
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave covers."""

    covers = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    for featureset_id, name in link.get_covers():
        try:
            covers.append(LWRF2Cover(name, featureset_id, link))
        except Exception as e: _LOGGER.exception("Could not add LWRF2Cover")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(covers)
    async_add_entities(covers)


class LWRF2Cover(CoverEntity):
    """Representation of a LightwaveRF cover."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, name, featureset_id, link):
        """Initialize LWRFCover entity."""
        _LOGGER.debug("Adding cover %s - %s ", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = COVER

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self)

        
        self._state = 50


    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    @property
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP

    async def async_update(self):
        """Update state"""
        self._state = 50

    @property
    def current_cover_position(self):
        """Lightwave cover position."""
        return self._state

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        is_closed = None
        return is_closed

    async def async_open_cover(self, **kwargs):
        """Open the Lightwave cover."""
        await self._lwlink.async_cover_open_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    async def async_close_cover(self, **kwargs):
        """Close the Lightwave cover."""
        await self._lwlink.async_cover_close_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    async def async_stop_cover(self, **kwargs):
        """Open the Lightwave cover."""
        await self._lwlink.async_cover_stop_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)