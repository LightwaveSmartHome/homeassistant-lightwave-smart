import logging
from .const import CONF_HOMEKIT
from homeassistant.components.switch import (
    SwitchEntity,
    SwitchDeviceClass,
    SwitchEntityDescription,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.core import callback
from .entity import LightwaveEntity, async_lightwave_command
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)


DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1

SOCKET = SwitchEntityDescription(
    key="smartSocket",
    device_class=SwitchDeviceClass.OUTLET,
    name=None,
    icon="mdi:power-socket-uk"
)

SWITCH = SwitchEntityDescription(
    key="smartSwitch",
    device_class=SwitchDeviceClass.SWITCH,
    name=None
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave switches."""

    switches = []
    link = config_entry.runtime_data.link

    homekit = config_entry.options.get(CONF_HOMEKIT, False)
    for featureset_id, name in link.get_switches():
        try:
            switches.append(LWRF2Switch(name, featureset_id, link, homekit, SWITCH))
        except Exception as e: _LOGGER.exception("Could not add switch LWRF2Switch")

    for featureset_id, name in link.get_sockets():
        try:
            switches.append(LWRF2Switch(name, featureset_id, link, homekit, SOCKET))
        except Exception as e: _LOGGER.exception("Could not add socket LWRF2Switch")

    config_entry.runtime_data.entities.extend(switches)
    async_add_entities(switches)

class LWRF2Switch(LightwaveEntity, SwitchEntity):
    """Representation of a LightwaveRF socket/switch."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, name, featureset_id, link, homekit, description):
        _LOGGER.debug("Adding socket/switch: %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link
        
        self.entity_description = description
        
        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._homekit = homekit

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        
        self._attr_device_info = make_entity_device_info(self, name)

        self._state = \
            self._featureset.features["switch"].state


    async def async_added_to_hass(self):
        """Subscribe to events."""
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        if self._homekit and self._gen2:
            if entity_entry is not None and not entity_entry.hidden:
                registry.async_update_entity(
                    self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        else:
            if entity_entry is not None and entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(self.entity_id, hidden_by=None)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        if kwargs["feature"] == "uiButton":
            _LOGGER.debug("Button (socket) press event: %s %s", self.entity_id, kwargs["new_value"])
            self.hass.bus.fire("lightwave_smart.click",{"entity_id": self.entity_id, "code": kwargs["new_value"]},
        )
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        self._state = self._featureset.features["switch"].state

    @property
    def is_on(self):
        """Lightwave switch is on state."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the Lightwave switch on."""
        await async_lightwave_command(
            self._lwlink.async_turn_on_by_featureset_id(self._featureset_id),
            "Could not turn the switch on",
        )
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the Lightwave switch off."""
        await async_lightwave_command(
            self._lwlink.async_turn_off_by_featureset_id(self._featureset_id),
            "Could not turn the switch off",
        )
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)
