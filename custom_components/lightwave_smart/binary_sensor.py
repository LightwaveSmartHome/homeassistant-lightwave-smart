from datetime import timedelta
import logging
from .const import CONF_HOMEKIT
from .entity import LightwaveEntity
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
# Device Classes
try:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
    DEVICE_CLASS_WINDOW = BinarySensorDeviceClass.WINDOW
    DEVICE_CLASS_PLUG = BinarySensorDeviceClass.PLUG
    DEVICE_CLASS_MOTION = BinarySensorDeviceClass.MOTION
    DEVICE_CLASS_HEAT = BinarySensorDeviceClass.HEAT
    DEVICE_CLASS_CONNECTIVITY = BinarySensorDeviceClass.CONNECTIVITY
except ImportError:
    from homeassistant.components.binary_sensor import (
        DEVICE_CLASS_WINDOW, DEVICE_CLASS_PLUG, DEVICE_CLASS_MOTION, DEVICE_CLASS_HEAT, DEVICE_CLASS_CONNECTIVITY
    )
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_registry as er
from .utils import (
    make_device_info,
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0
_LINK_LAST_EVENT_REFRESH_INTERVAL = timedelta(seconds=60)

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
    ),
    BinarySensorEntityDescription(
        key="callForHeat",
        device_class=DEVICE_CLASS_HEAT,
        name="Call for Heat",
    ),
]

# Features HomeKit Device already reports. uiDigitalInput is not one of them.
# callForHeat matches the thermostat current heating state, and the climate entity stays visible.
_HOMEKIT_HIDDEN_FEATURES = frozenset({
    "windowPosition",
    "outletInUse",
    "movement",
    "callForHeat",
})

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave sensors."""

    sensors = []
    link = config_entry.runtime_data.link

    homekit = config_entry.options.get(CONF_HOMEKIT, False)

    for featureset_id, featureset in link.featuresets.items():
        for description in SENSORS:
            if featureset.has_feature(description.key):
                try:
                    sensors.append(LWRF2BinarySensor(featureset.name, featureset_id, link, description, homekit))
                except Exception as e: _LOGGER.exception("Could not add LWRF2BinarySensor")
    
    for featureset_id, hubname in link.get_hubs():
        try:
            sensors.append(LWRF2LinkBinarySensor(hubname, featureset_id, link))
        except Exception:
            _LOGGER.exception("Could not add LWRF2LinkBinarySensor")

    for device_id in link.get_device_ids():
        device = link.devices[device_id]
        try:
            if device.is_gen2() and not device.is_hub():
                sensors.append(LWRF2DeviceConnectivitySensor(device))
        except Exception:
            _LOGGER.exception("Could not add LWRF2DeviceConnectivitySensor")

    config_entry.runtime_data.entities.extend(sensors)
    async_add_entities(sensors)

class LWRF2BinarySensor(LightwaveEntity, BinarySensorEntity):
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
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        hide = self._homekit and self.entity_description.key in _HOMEKIT_HIDDEN_FEATURES
        if hide:
            if entity_entry is not None and not entity_entry.hidden:
                registry.async_update_entity(
                    self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
        elif entity_entry is not None and entity_entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
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


class LWRF2DeviceConnectivitySensor(LightwaveEntity, BinarySensorEntity):
    """Whether the hub can reach a Smart Series device. Not hidden by the HomeKit option.

    One sensor per device. Every featureset on that device shares its radio.
    """

    _subscribe_featureset = False

    def __init__(self, device):
        _LOGGER.debug("Adding connectivity sensor %s - %s", device.name, device.device_id)
        self._device_id = device.device_id
        self._lwlink = device.link
        self._device = device
        self.entity_description = BinarySensorEntityDescription(
            key="connectivity",
            name="Connectivity",
            device_class=DEVICE_CLASS_CONNECTIVITY,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        self._attr_unique_id = f"{self._device_id}_connectivity"
        self._attr_device_info = make_device_info(self)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        await self._lwlink.async_register_connection_status_callback(
            self._device_id, self._async_changed
        )
        self.async_on_remove(
            lambda: self._lwlink.unregister_connection_status_callback(
                self._device_id, self._async_changed
            )
        )

    @callback
    def _async_changed(self, **kwargs):
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self._lwlink.link_is_latched_offline(self._device_id)
            and self._device.connection_status in ("connected", "disconnected")
        )

    @property
    def is_on(self):
        return self._device.connection_status == "connected"


class LWRF2LinkBinarySensor(LightwaveEntity, BinarySensorEntity):
    """Cloud link for a hub. Not hidden by the HomeKit option.

    This entity stays available when the websocket drops so it can report off.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _subscribe_featureset = False
    _attr_device_class = DEVICE_CLASS_CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Cloud Link"

    def __init__(self, name, featureset_id, link):
        _LOGGER.debug("Adding cloud link sensor %s - %s", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = BinarySensorEntityDescription(
            key="cloudLink",
            name="Cloud Link",
            device_class=DEVICE_CLASS_CONNECTIVITY,
            entity_category=EntityCategory.DIAGNOSTIC,
        )

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = False

        self._attr_unique_id = f"{self._featureset_id}_cloudLink"
        self._attr_device_info = make_entity_device_info(self, name)
        self._unsub_interval = None
        self._last_written_snapshot = None

    def _last_event_iso(self):
        getter = getattr(self._lwlink, "get_link_last_event", None)
        last_event = getter(self._featureset_id) if getter is not None else None
        return last_event.isoformat() if last_event is not None else None

    def _current_snapshot(self):
        return (self.is_on, self._last_event_iso())

    @property
    def available(self) -> bool:
        """Stay available so a dropped link is reported as off."""
        return True

    async def async_added_to_hass(self):
        """Subscribe to link up and down, and to this hub's websocket latch."""
        await super().async_added_to_hass()
        self._lwlink.register_connect_callback(self._async_link_changed)
        self._lwlink.register_disconnect_callback(self._async_link_changed)
        await self._lwlink.async_register_connection_status_callback(
            self._device.device_id, self._async_link_changed
        )
        self._last_written_snapshot = self._current_snapshot()
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_periodic_refresh,
            _LINK_LAST_EVENT_REFRESH_INTERVAL,
        )

    async def async_will_remove_from_hass(self):
        """Drop the link callbacks. A later add registers this instance once."""
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None
        self._lwlink.unregister_connect_callback(self._async_link_changed)
        self._lwlink.unregister_disconnect_callback(self._async_link_changed)
        self._lwlink.unregister_connection_status_callback(
            self._device.device_id, self._async_link_changed
        )
        await super().async_will_remove_from_hass()

    @callback
    def _async_link_changed(self, **kwargs):
        """Update after the Home Assistant websocket or this hub's websocket changes."""
        self._last_written_snapshot = self._current_snapshot()
        self.async_schedule_update_ha_state()

    @callback
    def _async_periodic_refresh(self, _now):
        """Write state at most once a minute when last_event or link state changed."""
        if self.hass is None:
            return
        snapshot = self._current_snapshot()
        if snapshot == self._last_written_snapshot:
            return
        self._last_written_snapshot = snapshot
        self.async_write_ha_state()

    @property
    def is_on(self):
        """On while Home Assistant can hear the backend and this hub's websocket is up.

        A hub websocket drop is latched after the library's grace period. A short blip stays on.
        """
        if not self._lwlink.connected:
            return False
        return not self._lwlink.link_is_latched_offline(self._device.device_id)

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        attribs = get_extra_state_attributes(self)
        last_event = self._last_event_iso()
        if last_event is not None:
            attribs["last_event"] = last_event
        return attribs
