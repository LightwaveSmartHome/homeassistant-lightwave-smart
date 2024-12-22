import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, SERVICE_SETBRIGHTNESS, CONF_HOMEKIT, DOMAIN
from homeassistant.components.light import (
    LightEntity,
    LightEntityDescription,
    ATTR_BRIGHTNESS, COLOR_MODE_BRIGHTNESS, COLOR_MODE_RGB
)
from homeassistant.core import callback
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from .utils import (
    make_device_info,
    get_extra_state_attributes
)
import voluptuous as vol


DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

LIGHT = LightEntityDescription(
    key="smartLightSwitch",
    name="Switch",
    has_entity_name=True
)

OFF_LED = LightEntityDescription(
    key="offLed",
    name="Off LED Indicator",
    icon="mdi:led-outline",
    has_entity_name=True,
    entity_category=EntityCategory.CONFIG
)

LED = LightEntityDescription(
    key="ledIndicator",
    name="LED Indicator",
    icon="mdi:led-outline",
    has_entity_name=True,
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave lights."""

    lights = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    homekit = config_entry.options.get(CONF_HOMEKIT, False)
    for featureset_id, name in link.get_lights():
        try:
            lights.append(LWRF2Light(name, featureset_id, link, homekit))
        except Exception as e: _LOGGER.exception("Could not add LWRF2Light")


    for featureset_id, name in link.get_lights():
        feature_set = link.featuresets[featureset_id]
        if feature_set.has_led():
            channel_input_mapped = None
            if feature_set.has_uiIndicator():
                ui_io_map_feature = feature_set.get_feature_by_type("uiIOMap")
                channel_input_mapped = ui_io_map_feature.channel_input_mapped
                
            try:
                if channel_input_mapped is not None and channel_input_mapped == False:
                    lights.append(LWRF2LED(name, featureset_id, link, LED, 'uiIndicator'))
                else:
                    lights.append(LWRF2LED(name, featureset_id, link, OFF_LED))
                    
            except Exception as e: _LOGGER.exception("Could not add LWRF2LED")

    for featureset_id, name in link.get_sockets():
        if link.featuresets[featureset_id].has_led():
            try:
                lights.append(LWRF2LED(name, featureset_id, link, OFF_LED))
            except Exception as e: _LOGGER.exception("Could not add LWRF2LED")

    for featureset_id, name in link.get_hubs():
        if link.featuresets[featureset_id].has_led():
            try:
                lights.append(LWRF2LED(name, featureset_id, link, OFF_LED))
            except Exception as e: _LOGGER.exception("Could not add LWRF2LED")
            

    async def service_handle_brightness(light, call):
        _LOGGER.debug(f"Received service call set brightness - {light}")
        
        brightness = int(round(call.data.get("brightness") / 255 * 100))
        feature_id = link.featuresets[light._featureset_id].features['dimLevel'].id
        await link.async_write_feature(feature_id, brightness)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SETBRIGHTNESS, 
        {
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }, 
        service_handle_brightness, 
    )

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(lights)
    async_add_entities(lights)


class LWRF2Light(LightEntity):
    """Representation of a LightwaveRF light."""

    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, homekit):
        _LOGGER.debug("Adding light: %s - %s ", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        for hub_featureset_id, hubname in self._lwlink.get_hubs():
            self._linkid = hub_featureset_id

        self.entity_description = LIGHT

        self._homekit = homekit

        self._gen2 = self._lwlink.featuresets[self._featureset_id].is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_device_info(self, name)


        self._state = \
            self._lwlink.featuresets[self._featureset_id].features["switch"].state
        
        dimLevel = self._lwlink.featuresets[self._featureset_id].features["dimLevel"].state
        self._brightness = int(round(dimLevel / 100 * 255)) if dimLevel is not None else None
        
        self._has_led = self._lwlink.featuresets[self._featureset_id].has_led()
        

    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        if self._homekit and self._gen2:
            if entity_entry is not None and not entity_entry.hidden:
                registry.async_update_entity(
                    self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        else:
            if entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(self.entity_id, hidden_by=None)

    #TODO add async_will_remove_from_hass() to clean up

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        if kwargs["feature"] == "uiButtonPair":
            _LOGGER.debug("Button (light) press event: %s %s", self.entity_id, kwargs["new_value"])
            self.hass.bus.fire("lightwave_smart.click",{"entity_id": self.entity_id, "code": kwargs["new_value"]},
        )
        self.async_schedule_update_ha_state(True)

    @property
    def supported_color_modes(self):
        """Flag supported features."""
        return {COLOR_MODE_BRIGHTNESS}

    @property
    def color_mode(self):
        """Flag supported features."""
        return COLOR_MODE_BRIGHTNESS

    async def async_update(self):
        """Update state"""
        self._state = \
            self._lwlink.featuresets[self._featureset_id].features["switch"].state
        dimLevel = self._lwlink.featuresets[self._featureset_id].features["dimLevel"].state
        self._brightness = int(round(dimLevel / 100 * 255)) if dimLevel is not None else None

    @property
    def brightness(self):
        """Return the brightness of the group lights."""
        return self._brightness

    @property
    def is_on(self):
        """Lightwave switch is on state."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the Lightwave light on."""
        _LOGGER.debug("HA light.turn_on received, kwargs: %s", kwargs)

        if ATTR_BRIGHTNESS in kwargs:
            _LOGGER.debug("Changing brightness from %s to %s (%s%%)", self._brightness, kwargs[ATTR_BRIGHTNESS], int(kwargs[ATTR_BRIGHTNESS] / 255 * 100))
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            await self._lwlink.async_set_brightness_by_featureset_id(
                self._featureset_id, int(round(self._brightness / 255 * 100)))

        self._state = True
        await self._lwlink.async_turn_on_by_featureset_id(self._featureset_id)

        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the Lightwave light off."""
        _LOGGER.debug("HA light.turn_off received, kwargs: %s", kwargs)

        self._state = False
        await self._lwlink.async_turn_off_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    async def async_set_rgb(self, led_rgb):
        await self._lwlink.async_set_led_rgb_by_featureset_id(self._featureset_id, led_rgb)

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)


class LWRF2LED(LightEntity):
    """Representation of a LightwaveRF LED."""

    # _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, description, feature_type='rgbColor'):
        _LOGGER.debug("Adding LED (%s): %s - %s ", description.key, name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        for hub_featureset_id, hubname in self._lwlink.get_hubs():
            self._linkid = hub_featureset_id

        self.entity_description = description

        self._gen2 = self._lwlink.featuresets[self._featureset_id].is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_device_info(self, name)
        
        self.feature_type = feature_type

        # feature_type uiIndicator is not readable from Link (though server may have cache), events are generated when its changed
        color = \
            self._lwlink.featuresets[self._featureset_id].features[self.feature_type].state
        if color == 0 or not color:
            self._state = False
            self._r = 255
            self._g = 255
            self._b = 255
        else:
            self._state = True
            self._r = color // 65536
            self._g = (color - self._r * 65536) // 256
            self._b = (color - self._r * 65536 - self._g * 256)
        self._brightness = max(self._r, self._g, self._b)
        self._r = int(self._r * 255 / self._brightness)
        self._g = int(self._g * 255 / self._brightness)
        self._b = int(self._b * 255 / self._brightness)


    async def async_added_to_hass(self):
        """Subscribe to events."""
        _LOGGER.debug("async_added_to_hass - for %s ", self._featureset_id)
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

    #TODO add async_will_remove_from_hass() to clean up

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        _LOGGER.debug("async_update_callback - for %s - %s ", self._featureset_id, kwargs)
        self.async_schedule_update_ha_state(True)

    @property
    def supported_color_modes(self):
        """Flag supported features."""
        return {COLOR_MODE_RGB}

    @property
    def color_mode(self):
        """Flag supported features."""
        return COLOR_MODE_RGB

    async def async_update(self):
        """Update state"""
        color = \
            self._lwlink.featuresets[self._featureset_id].features[self.feature_type].state
        
        if color == 0 or not color:
            self._state = False
        else:
            self._state = True
            self._r = color // 65536
            self._g = (color - self._r * 65536) //256
            self._b = (color - self._r * 65536 - self._g * 256)
            self._brightness = max(self._r, self._g, self._b)
            self._r = int(self._r * 255 / self._brightness)
            self._g = int(self._g * 255 / self._brightness)
            self._b = int(self._b * 255 / self._brightness)

    @property
    def brightness(self):
        """Return the brightness of the group lights."""
        return self._brightness

    @property
    def rgb_color(self):
        return (self._r, self._g, self._b)

    @property
    def is_on(self):
        """Lightwave switch is on state."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn the Lightwave LED on / set rgb_color / set brightness."""
        
        self._state = True
        if 'rgb_color' in kwargs:
            self._r = kwargs['rgb_color'][0]
            self._g = kwargs['rgb_color'][1]
            self._b = kwargs['rgb_color'][2]
        
        if 'brightness' in kwargs:
            self._brightness = kwargs['brightness']

        r = int(self._r * self._brightness /255)
        g = int(self._g * self._brightness /255)
        b = int(self._b * self._brightness /255)
        rgb = r * 65536 + g * 256 + b

        await self._lwlink.async_set_led_rgb_by_featureset_id(self._featureset_id, rgb, self.feature_type)

        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the Lightwave LED off."""
        _LOGGER.debug("HA led.turn_off received, kwargs: %s", kwargs)
        self._state = False
        await self._lwlink.async_set_led_rgb_by_featureset_id(self._featureset_id, 0, self.feature_type)

        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)