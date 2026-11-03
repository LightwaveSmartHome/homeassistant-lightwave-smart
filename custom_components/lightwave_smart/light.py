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
    make_device_info,
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

UI_LED_ON = "uiLEDOnColour"
UI_LED_OFF = "uiLEDOffColour"

ON_LED = LightEntityDescription(
    key=UI_LED_ON,
    name="On LED Indicator",
    icon="mdi:led-on",
    has_entity_name=True,
    entity_category=EntityCategory.CONFIG
)

OFF_UI_LED = LightEntityDescription(
    key=UI_LED_OFF,
    name="Off LED Indicator",
    icon="mdi:led-outline",
    has_entity_name=True,
    entity_category=EntityCategory.CONFIG
)

def _ui_led_featuresets(link):
    """Gang featuresets that use uiLEDOnColour and uiLEDOffColour."""
    found = []
    seen = set()
    for device in link.devices.values():
        for featureset in device.featuresets:
            if featureset.featureset_id in seen:
                continue
            if not (
                featureset.has_feature(UI_LED_ON)
                and featureset.has_feature(UI_LED_OFF)
                and not featureset.virtual_product_code
            ):
                continue
            seen.add(featureset.featureset_id)
            found.append(featureset)
    return found

def _remove_light_entity(entity_registry, unique_id):
    entity_id = entity_registry.async_get_entity_id("light", DOMAIN, unique_id)
    if entity_id is not None:
        entity_registry.async_remove(entity_id)

def _led_featuresets_by_device(link):
    """Featuresets that expose rgbColor, grouped by physical device.

    A multi-gang plate has one status LED. Each gang featureset still
    carries rgbColor, so callers must create a single entity per device.
    """
    groups = []
    for device in link.devices.values():
        featuresets = []
        seen = set()
        for featureset in device.featuresets:
            if not featureset.has_led() or featureset.featureset_id in seen:
                continue
            seen.add(featureset.featureset_id)
            featuresets.append(featureset)
        if featuresets:
            groups.append(featuresets)
    return groups

def _migrate_off_led_unique_id(entity_registry, featuresets, new_unique_id):
    """Keep one existing Off LED entity when its unique id changes.

    Renames, entity ids, and disabled flags stay on that entity. Other
    gangs are removed by the caller.
    """
    if entity_registry.async_get_entity_id("light", DOMAIN, new_unique_id):
        return
    for featureset in featuresets:
        old_unique_id = f"{featureset.featureset_id}_{OFF_LED.key}"
        if old_unique_id == new_unique_id:
            continue
        entity_id = entity_registry.async_get_entity_id("light", DOMAIN, old_unique_id)
        if entity_id is None:
            continue
        entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)
        return

def _remove_stale_led_entities(entity_registry, featuresets, keep_unique_id):
    """Drop per-gang Off LED entities and the old uiIndicator ledIndicator."""
    for featureset in featuresets:
        for suffix in (OFF_LED.key, "ledIndicator"):
            unique_id = f"{featureset.featureset_id}_{suffix}"
            if unique_id == keep_unique_id:
                continue
            _remove_light_entity(entity_registry, unique_id)

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


    entity_registry = er.async_get(hass)
    ui_led_featuresets = _ui_led_featuresets(link)
    ui_led_device_ids = {featureset.device.device_id for featureset in ui_led_featuresets}

    for featuresets in _led_featuresets_by_device(link):
        device_id = featuresets[0].device.device_id
        try:
            if device_id in ui_led_device_ids:
                _remove_stale_led_entities(entity_registry, featuresets, None)
                _remove_light_entity(entity_registry, f"{device_id}_{OFF_LED.key}")
                continue
            led = LWRF2LED(featuresets, link, OFF_LED)
            _migrate_off_led_unique_id(entity_registry, featuresets, led.unique_id)
            _remove_stale_led_entities(entity_registry, featuresets, led.unique_id)
            lights.append(led)
        except Exception:
            _LOGGER.exception(
                "Could not add LWRF2LED for device %s",
                device_id,
            )

    for featureset in ui_led_featuresets:
        try:
            lights.append(LWRF2LED([featureset], link, ON_LED, UI_LED_ON, per_gang=True))
            lights.append(LWRF2LED([featureset], link, OFF_UI_LED, UI_LED_OFF, per_gang=True))
        except Exception:
            _LOGGER.exception(
                "Could not add MK4 LED indicators for featureset %s",
                featureset.featureset_id,
            )
            

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

    Function mode comes from colourSetup:
      - White (0): brightness only.
      - RGB (1) and RGB with White (2): hue, saturation, and brightness.
        Saturation is whiteness: 0 is white, 100 is full colour.
      - Warm White Cool White (3): colour temperature and brightness.

    RGB with White and CCT are tested on L84V24 hardware. White and RGB have
    not been checked on a device.
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
        self._last_brightness = self._brightness or None

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
        if kwargs["feature"] == "uiButtonPair":
            _LOGGER.debug("Button (light) press event: %s %s", self.entity_id, kwargs["new_value"])
            self.hass.bus.fire("lightwave_smart.click",{"entity_id": self.entity_id, "code": kwargs["new_value"]},
        )
        self.async_schedule_update_ha_state(True)

    @property
    def _colour_setup(self):
        """Return colourSetup as an int (0=White, 1=RGB, 2=RGBW, 3=CCT), or None."""
        feature = self._featureset.features.get("colourSetup")
        if feature is None or feature.state is None:
            return None
        try:
            return int(feature.state)
        except (TypeError, ValueError):
            return None

    def _dim_level_for_device(self, brightness):
        """Convert HA brightness to dimLevel. Never send 0; that clears colour."""
        level = int(round(brightness / 255 * 100))
        return min(100, max(1, level))

    def _hs_writes(self, hue, saturation):
        """Order the colourHue and colourSaturation writes for a colour change.

        The driver fades to each value as it arrives, so the order shows. Going
        towards white, lower saturation first, and skip hue at saturation 0 where
        it means nothing; otherwise the strip sweeps round the colour wheel on the
        way (blue to white passes through cyan and green). Going towards colour,
        set hue first while the strip is still pale.
        """
        if saturation == 0:
            return [("colourSaturation", 0)]
        try:
            current = float(self._featureset.features["colourSaturation"].state)
        except (TypeError, ValueError):
            current = 100
        writes = [("colourHue", hue), ("colourSaturation", saturation)]
        if saturation < current:
            writes.reverse()
        return writes

    @property
    def color_mode(self):
        setup = self._colour_setup
        if setup == 0:
            return ColorMode.BRIGHTNESS
        if setup in (1, 2):
            return ColorMode.HS
        # CCT, and the fallback while colourSetup has not loaded yet.
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self):
        """Expose only the controls for this function mode.

        Until colourSetup has loaded, both colour controls stay available so the
        entity does not drop a mode and then add it back.
        """
        setup = self._colour_setup
        if setup == 0:
            return {ColorMode.BRIGHTNESS}
        if setup in (1, 2):
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
        if self._brightness:
            self._last_brightness = self._brightness

        ct = self._featureset.features["colourTemperature"].state
        self._color_temp_kelvin = round(1_000_000 / ct) if ct else None

        hue = self._featureset.features["colourHue"].state
        sat = self._featureset.features["colourSaturation"].state
        self._hs_color = (float(hue), float(sat)) if hue is not None and sat is not None else None

    async def async_turn_on(self, **kwargs):
        """Turn the LED driver on, optionally setting brightness and/or colour."""
        _LOGGER.debug("LED driver turn_on %s kwargs: %s", self._featureset_id, kwargs)

        if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] == 0:
            # Writing dimLevel=0 resets colourHue/colourSaturation to 0 on the device,
            # destroying colour state. Use switch=0 instead to preserve colour for next on.
            await self.async_turn_off()
            return

        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None and not self._brightness:
            # The app can leave switch=1 at dimLevel 0, which is reported as off.
            # A toggle sends no brightness, so restore the last level above 0.
            brightness = self._last_brightness or 255

        if brightness is not None:
            self._brightness = brightness
            if brightness:
                self._last_brightness = brightness
            await self._lwlink.async_set_brightness_by_featureset_id(
                self._featureset_id, self._dim_level_for_device(brightness))

        modes = self.supported_color_modes
        if ATTR_COLOR_TEMP_KELVIN in kwargs and ColorMode.COLOR_TEMP in modes:
            kelvin = max(_LED_DRIVER_MIN_COLOUR_TEMP_K,
                         min(_LED_DRIVER_MAX_COLOUR_TEMP_K, kwargs[ATTR_COLOR_TEMP_KELVIN]))
            self._color_temp_kelvin = kelvin
            await self._lwlink.async_write_feature_by_name(
                self._featureset_id, "colourTemperature", round(1_000_000 / kelvin))

        if ATTR_HS_COLOR in kwargs and ColorMode.HS in modes:
            h, s = kwargs[ATTR_HS_COLOR]
            hue, saturation = int(round(h)), int(round(s))
            if saturation == 0:
                # Hue is not written, so report the hue still on the device.
                device_hue = self._featureset.features["colourHue"].state
                try:
                    reported_hue = float(device_hue)
                except (TypeError, ValueError):
                    reported_hue = float(h)
                self._hs_color = (reported_hue, 0.0)
            else:
                self._hs_color = (h, s)
            for feature, value in self._hs_writes(hue, saturation):
                await self._lwlink.async_write_feature_by_name(
                    self._featureset_id, feature, value)

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
    """Locator LED colour for a Lightwave plate.

    rgbColor is one setting for the whole plate. Multi-gang dimmers and
    sockets repeat it on every gang, so that entity reads and writes the
    first featureset and still follows updates reported on the others.

    MK4 gangs use uiLEDOnColour and uiLEDOffColour. Those stay on the gang,
    because each gang can have its own on and off colour.
    """

    # _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, featuresets, link, description, feature_type='rgbColor', per_gang=False):
        self._featuresets = featuresets
        self._featureset = featuresets[0]
        self._featureset_id = self._featureset.featureset_id
        self._color_featureset = self._featureset
        self._lwlink = link

        self.entity_description = description

        self._device = self._featureset.device

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        if per_gang:
            self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
            self._attr_device_info = make_entity_device_info(self, self._featureset.name)
        else:
            self._attr_unique_id = f"{self._device.device_id}_{self.entity_description.key}"
            # Hubs are registered against their featureset. Plates use the parent
            # device, the same one as the firmware entity.
            if self._device.is_hub():
                self._attr_device_info = make_entity_device_info(self)
            else:
                self._attr_device_info = make_device_info(self)

        self.feature_type = feature_type

        _LOGGER.debug(
            "Adding LED (%s): %s via %s (%d featuresets)",
            description.key,
            self._device.device_id,
            self._featureset_id,
            len(featuresets),
        )

        self._state = False
        self._r = 255
        self._g = 255
        self._b = 255
        self._brightness = 255
        self._apply_color(self._featureset.features[self.feature_type].state)

    def _apply_color(self, color, *, keep_last_when_off=False):
        """Decode a packed rgbColor integer into on/off, colour, and brightness."""
        if color == 0 or not color:
            self._state = False
            if not keep_last_when_off:
                self._r = 255
                self._g = 255
                self._b = 255
                self._brightness = 255
            return

        self._state = True
        self._r = color // 65536
        self._g = (color - self._r * 65536) // 256
        self._b = color - self._r * 65536 - self._g * 256
        self._brightness = max(self._r, self._g, self._b)
        if self._brightness == 0:
            self._state = False
            return
        self._r = int(self._r * 255 / self._brightness)
        self._g = int(self._g * 255 / self._brightness)
        self._b = int(self._b * 255 / self._brightness)

    def _featureset_for_feature_id(self, feature_id):
        for featureset in self._featuresets:
            feature = featureset.features.get(self.feature_type)
            if feature is not None and feature.id == feature_id:
                return featureset
        return None

    async def async_added_to_hass(self):
        """Subscribe to events from every gang that exposes this LED."""
        _LOGGER.debug("async_added_to_hass - for %s ", self._device.device_id)
        for featureset in self._featuresets:
            await self._lwlink.async_register_feature_callback(
                featureset.featureset_id, self.async_update_callback
            )

    #TODO add async_will_remove_from_hass() to clean up

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        _LOGGER.debug("async_update_callback - for %s - %s ", self._device.device_id, kwargs)
        if kwargs.get("feature") == self.feature_type:
            source = self._featureset_for_feature_id(kwargs.get("feature_id"))
            if source is not None:
                self._color_featureset = source
            self._apply_color(kwargs.get("new_value"), keep_last_when_off=True)
            self.async_write_ha_state()
            return
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
        """Update state from the featureset that last reported rgbColor."""
        self._apply_color(
            self._color_featureset.features[self.feature_type].state,
            keep_last_when_off=True,
        )

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