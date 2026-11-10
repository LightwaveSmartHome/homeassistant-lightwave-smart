import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, DOMAIN
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

# colourSetup values used by the Lightwave app. See docs/L84V24x-feature-types.md.
_COLOUR_SETUPS = (
    "White",
    "RGB",
    "RGB with White",
    "Warm White Cool White (CCT)",
)
_DEFAULT_OUTPUT_MAP = (
    0x00000F00,
    0x0000F543,
    0x00000543,
    0x0000FF21,
)

_ROLE_WHITE = 0
_ROLE_WARM = 1
_ROLE_COLD = 2
_ROLE_RED = 3
_ROLE_GREEN = 4
_ROLE_BLUE = 5

COLOUR_SETUP = SelectEntityDescription(
    key="colourSetup",
    name="Function",
    options=list(_COLOUR_SETUPS),
    entity_category=EntityCategory.CONFIG,
)


def _output_roles(value):
    """Return the four output roles, output 1 first, or None if the value is not an int."""
    try:
        packed = int(value) & 0xFFFF
    except (TypeError, ValueError):
        return None
    return [(packed >> shift) & 0xF for shift in (0, 4, 8, 12)]


def _map_matches_mode(value, mode):
    """Return True when colourOutputMap satisfies the rules for this colourSetup."""
    roles = _output_roles(value)
    if roles is None:
        return False
    counts = {}
    for role in roles:
        counts[role] = counts.get(role, 0) + 1
    if mode == 0:
        return counts.get(_ROLE_WHITE, 0) >= 1
    if mode == 1:
        return (
            counts.get(_ROLE_RED, 0) == 1
            and counts.get(_ROLE_GREEN, 0) == 1
            and counts.get(_ROLE_BLUE, 0) == 1
        )
    if mode == 2:
        return (
            counts.get(_ROLE_WHITE, 0) == 1
            and counts.get(_ROLE_RED, 0) == 1
            and counts.get(_ROLE_GREEN, 0) == 1
            and counts.get(_ROLE_BLUE, 0) == 1
        )
    if mode == 3:
        return counts.get(_ROLE_WARM, 0) == 1 and counts.get(_ROLE_COLD, 0) == 1
    return False


def _is_led_driver(featureset):
    """Same features the light platform uses to create an LED driver."""
    return (
        featureset.has_feature("colourHue")
        and featureset.has_feature("colourSetup")
        and featureset.has_feature("colourTemperature")
    )


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave LED driver function selects."""

    selects = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    for featureset_id, featureset in link.featuresets.items():
        if not _is_led_driver(featureset):
            continue
        try:
            selects.append(LWRF2ColourSetup(featureset.name, featureset_id, link))
        except Exception:
            _LOGGER.exception("Could not add LWRF2ColourSetup")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(selects)
    async_add_entities(selects)


class LWRF2ColourSetup(SelectEntity):
    """Function mode for a Lightwave LED driver.

    The current option stays unknown until colourSetup has a value.
    Changing mode writes the default colourOutputMap only when the current
    map does not match the new mode. A matching custom map is left alone.
    An unread map is left alone, so a value that has not arrived yet is not
    replaced with the default.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_options = list(_COLOUR_SETUPS)

    def __init__(self, name, featureset_id, link):
        _LOGGER.debug("Adding colour setup: %s - %s", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = COLOUR_SETUP

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)

        self._mode = self._mode_from_feature()

    def _mode_from_feature(self):
        feature = self._featureset.features.get("colourSetup")
        if feature is None or feature.state is None:
            return None
        try:
            mode = int(feature.state)
        except (TypeError, ValueError):
            return None
        if mode < 0 or mode >= len(_COLOUR_SETUPS):
            return None
        return mode

    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state from colourSetup."""
        self._mode = self._mode_from_feature()

    @property
    def current_option(self):
        """Return the function mode, or None while colourSetup has not loaded."""
        if self._mode is None:
            return None
        return _COLOUR_SETUPS[self._mode]

    async def async_select_option(self, option: str) -> None:
        """Set the function mode and the default output map when required."""
        mode = _COLOUR_SETUPS.index(option)
        _LOGGER.debug("Setting colourSetup %s to %s", self._featureset_id, mode)

        output_map = self._featureset.features.get("colourOutputMap")
        write_map = (
            output_map is not None
            and output_map.state is not None
            and not _map_matches_mode(output_map.state, mode)
        )

        self._mode = mode
        await self._lwlink.async_write_feature_by_name(self._featureset_id, "colourSetup", mode)
        if write_map:
            _LOGGER.debug(
                "colourOutputMap for %s does not match mode %s, writing default %s",
                self._featureset_id,
                mode,
                _DEFAULT_OUTPUT_MAP[mode],
            )
            await self._lwlink.async_write_feature_by_name(
                self._featureset_id, "colourOutputMap", _DEFAULT_OUTPUT_MAP[mode]
            )

        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)
