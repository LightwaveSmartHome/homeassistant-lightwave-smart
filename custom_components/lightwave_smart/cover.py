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

# threeWayRelay: 1 open, 2 close, 0 stop. The relay does not report position.
_RELAY_OPEN = 1
_RELAY_CLOSE = 2

COVER = CoverEntityDescription(
    key="cover",
    name=None,
    device_class=CoverDeviceClass.BLIND,
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
        self._attr_device_info = make_entity_device_info(self, name)

    def _relay(self):
        """Return threeWayRelay as an int, or None when it has not been read."""
        feature = self._featureset.features.get("threeWayRelay")
        if feature is None or feature.state is None:
            return None
        try:
            return int(feature.state)
        except (TypeError, ValueError):
            return None

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
        """State is read from threeWayRelay when Home Assistant asks."""

    @property
    def current_cover_position(self):
        """Lightwave covers do not report a position."""
        return None

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return self._relay() == _RELAY_OPEN

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return self._relay() == _RELAY_CLOSE

    @property
    def is_closed(self):
        """The relay does not report whether the cover is closed."""
        return None

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