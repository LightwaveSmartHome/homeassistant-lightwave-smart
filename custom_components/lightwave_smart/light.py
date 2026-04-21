import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, SERVICE_SETBRIGHTNESS, CONF_HOMEKIT, DOMAIN
from homeassistant.components.light import (
    LightEntity,
    LightEntityDescription,
    ATTR_BRIGHTNESS, ATTR_COLOR_TEMP_KELVIN, ATTR_HS_COLOR, ColorMode
)
from homeassistant.core import callback
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from .utils import (
    make_entity_device_info,
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

LED_DRIVER = LightEntityDescription(
    key="smartLightSwitch",
    name="Light",
    has_entity_name=True
)

# Colour temperature range for LED drivers (mireds: 1,000,000 / kelvin)
# Verified from L84V24P200 hardware: 2000K (500 mireds) warm → 9800K (102 mireds) cool
_LED_DRIVER_MIN_COLOUR_TEMP_K = 2000   # warmest (500 mireds)
_LED_DRIVER_MAX_COLOUR_TEMP_K = 9800   # coolest (102 mireds)

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
        featureset = link.featuresets[featureset_id]
        try:
            if (featureset.has_feature('colourHue')
                    and featureset.has_feature('colourSetup')
                    and featureset.has_feature('colourTemperature')):
                lights.append(LWRF2LEDDriver(name, featureset_id, link, homekit))
            else:
                lights.append(LWRF2Light(name, featureset_id, link, homekit))
        except Exception as e:
            _LOGGER.exception("Could not add light for featureset %s", featureset_id)


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
        feature_id = light._featureset.features['dimLevel'].id
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


class LWRF2LEDDriver(LightEntity):
    """Representation of a Lightwave LED driver.

    Supports and tested with Function modes, identified by the colourSetup feature:
      - "RGB with White" (colourSetup=2)
        ColorMode.HS:
        Supports brightness and colour control (hue/saturation) of RGB channels.

      - "Warm White Cool White (CCT)" (colourSetup=3)
        ColorMode.COLOR_TEMP:
        Supports brightness and colour temperature control of white channel.

    Other Function modes ("White" & "RGB") are untested.
    """

    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, homekit):
        _LOGGER.debug("Adding LED driver: %s - %s", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = LED_DRIVER

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._homekit = homekit

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)

        self._state = self._featureset.features["switch"].state

        dim = self._featureset.features["dimLevel"].state
        self._brightness = int(round(dim / 100 * 255)) if dim is not None else None

        ct = self._featureset.features["colourTemperature"].state
        self._color_temp_kelvin = round(1_000_000 / ct) if ct else None

        hue = self._featureset.features["colourHue"].state
        sat = self._featureset.features["colourSaturation"].state
        self._hs_color = (float(hue), float(sat)) if hue is not None and sat is not None else None

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
            if entity_entry is not None and entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
                registry.async_update_entity(self.entity_id, hidden_by=None)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    @property
    def _colour_setup(self):
        """Return the colourSetup feature state (2=RGBW, 3=CCT), or None if not yet loaded."""
        feature = self._featureset.features.get("colourSetup")
        return feature.state if feature else None

    @property
    def color_mode(self):
        setup = self._colour_setup
        if setup == 2:
            return ColorMode.HS
        # colourSetup==3 is CCT; also default to COLOR_TEMP when state not yet loaded
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self):
        # Return only the mode relevant to this device's wiring so HA hides inapplicable controls.
        # Falls back to both until colourSetup is populated by the background state update task.
        setup = self._colour_setup
        if setup == 2:
            return {ColorMode.HS}
        if setup == 3:
            return {ColorMode.COLOR_TEMP}
        return {ColorMode.COLOR_TEMP, ColorMode.HS}

    @property
    def min_color_temp_kelvin(self):
        return _LED_DRIVER_MIN_COLOUR_TEMP_K

    @property
    def max_color_temp_kelvin(self):
        return _LED_DRIVER_MAX_COLOUR_TEMP_K

    @property
    def color_temp_kelvin(self):
        return self._color_temp_kelvin

    @property
    def hs_color(self):
        return self._hs_color

    @property
    def brightness(self):
        return self._brightness

    @property
    def is_on(self):
        # Treat dimLevel==0 as off: the Lightwave app leaves switch=1 at 0% brightness
        # but the light is effectively off. Brightness of 0 (HA scale) means off.
        if self._brightness is not None and self._brightness == 0:
            return False
        return self._state

    async def async_update(self):
        """Update state from featureset."""
        self._state = self._featureset.features["switch"].state

        dim = self._featureset.features["dimLevel"].state
        self._brightness = int(round(dim / 100 * 255)) if dim is not None else None

        ct = self._featureset.features["colourTemperature"].state
        self._color_temp_kelvin = round(1_000_000 / ct) if ct else None

        hue = self._featureset.features["colourHue"].state
        sat = self._featureset.features["colourSaturation"].state
        self._hs_color = (float(hue), float(sat)) if hue is not None and sat is not None else None

    async def async_turn_on(self, **kwargs):
        """Turn the LED driver on, optionally setting brightness and/or colour."""
        _LOGGER.debug("LED driver turn_on %s kwargs: %s", self._featureset_id, kwargs)

        if ATTR_BRIGHTNESS in kwargs:
            self._brightness = kwargs[ATTR_BRIGHTNESS]
            if self._brightness == 0:
                # Writing dimLevel=0 resets colourHue/colourSaturation to 0 on the device,
                # destroying colour state. Use switch=0 instead to preserve colour for next on.
                await self.async_turn_off()
                return
            await self._lwlink.async_set_brightness_by_featureset_id(
                self._featureset_id, int(round(self._brightness / 255 * 100)))

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(_LED_DRIVER_MIN_COLOUR_TEMP_K,
                         min(_LED_DRIVER_MAX_COLOUR_TEMP_K, kwargs[ATTR_COLOR_TEMP_KELVIN]))
            self._color_temp_kelvin = kelvin
            await self._lwlink.async_write_feature_by_name(
                self._featureset_id, "colourTemperature", round(1_000_000 / kelvin))

        if ATTR_HS_COLOR in kwargs:
            h, s = kwargs[ATTR_HS_COLOR]
            self._hs_color = (h, s)
            await self._lwlink.async_write_feature_by_name(
                self._featureset_id, "colourHue", int(round(h)))
            await self._lwlink.async_write_feature_by_name(
                self._featureset_id, "colourSaturation", int(round(s)))

        self._state = True
        await self._lwlink.async_turn_on_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the LED driver off."""
        _LOGGER.debug("LED driver turn_off %s", self._featureset_id)
        self._state = False
        await self._lwlink.async_turn_off_by_featureset_id(self._featureset_id)
        self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)


class LWRF2Light(LightEntity):
    """Representation of a LightwaveRF light."""

    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, homekit):
        _LOGGER.debug("Adding light: %s - %s ", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = LIGHT
        
        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._homekit = homekit

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        
        self._attr_device_info = make_entity_device_info(self, name)

        self._state = \
            self._featureset.features["switch"].state
        
        dimLevel = self._featureset.features["dimLevel"].state
        self._brightness = int(round(dimLevel / 100 * 255)) if dimLevel is not None else None
        
        self._has_led = self._featureset.has_led()
        

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
            if entity_entry is not None and entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
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
        return {ColorMode.BRIGHTNESS}

    @property
    def color_mode(self):
        """Flag supported features."""
        return ColorMode.BRIGHTNESS

    async def async_update(self):
        """Update state"""
        self._state = \
            self._featureset.features["switch"].state
        dimLevel = self._featureset.features["dimLevel"].state
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
            _LOGGER.debug(f"Changing brightness from {self._brightness} to {kwargs[ATTR_BRIGHTNESS]} ({int(kwargs[ATTR_BRIGHTNESS] / 255 * 100)}%)")
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

        self.entity_description = description
        
        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        
        self._attr_device_info = make_entity_device_info(self, name)
        
        self.feature_type = feature_type

        # feature_type uiIndicator is not readable from Link (though server may have cache), events are generated when its changed
        color = \
            self._featureset.features[self.feature_type].state
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
        return {ColorMode.RGB}

    @property
    def color_mode(self):
        """Flag supported features."""
        return ColorMode.RGB

    async def async_update(self):
        """Update state"""
        color = \
            self._featureset.features[self.feature_type].state
        
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