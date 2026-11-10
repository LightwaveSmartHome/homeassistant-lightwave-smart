import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, DOMAIN
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
# State Classes
try:
    from homeassistant.components.sensor import SensorStateClass
    STATE_CLASS_MEASUREMENT = SensorStateClass.MEASUREMENT
    STATE_CLASS_TOTAL_INCREASING = SensorStateClass.TOTAL_INCREASING
except ImportError:
    from homeassistant.components.sensor import  STATE_CLASS_MEASUREMENT, STATE_CLASS_TOTAL_INCREASING
# Device Classes
try:
    from homeassistant.components.sensor import SensorDeviceClass
    DEVICE_CLASS_BATTERY = SensorDeviceClass.BATTERY
    DEVICE_CLASS_CURRENT = SensorDeviceClass.CURRENT
    DEVICE_CLASS_ENERGY = SensorDeviceClass.ENERGY
    DEVICE_CLASS_ILLUMINANCE = SensorDeviceClass.ILLUMINANCE
    DEVICE_CLASS_POWER = SensorDeviceClass.POWER
    DEVICE_CLASS_SIGNAL_STRENGTH = SensorDeviceClass.SIGNAL_STRENGTH
    DEVICE_CLASS_TIMESTAMP = SensorDeviceClass.TIMESTAMP
    DEVICE_CLASS_VOLTAGE = SensorDeviceClass.VOLTAGE
except ImportError:
    from homeassistant.components.sensor import (
        DEVICE_CLASS_BATTERY, 
        DEVICE_CLASS_CURRENT, 
        DEVICE_CLASS_ENERGY, 
        DEVICE_CLASS_ILLUMINANCE, 
        DEVICE_CLASS_POWER, 
        DEVICE_CLASS_SIGNAL_STRENGTH, 
        DEVICE_CLASS_TIMESTAMP, 
        DEVICE_CLASS_VOLTAGE
    )
# Units
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, LIGHT_LUX
try:
    from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfPower
    ELECTRIC_CURRENT_MILLIAMPERE = UnitOfElectricCurrent.MILLIAMPERE
    ELECTRIC_POTENTIAL_VOLT = UnitOfElectricPotential.VOLT
    ENERGY_WATT_HOUR = UnitOfEnergy.WATT_HOUR
    POWER_WATT = UnitOfPower.WATT
except ImportError:
    from homeassistant.const import (POWER_WATT, ENERGY_WATT_HOUR, ELECTRIC_POTENTIAL_VOLT, ELECTRIC_CURRENT_MILLIAMPERE)

from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import ConfigEntryNotReady
from datetime import datetime, timedelta, timezone
import pytz
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

RECOMMENDED_LUX_LEVEL = 300

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)


SENSORS_PRIMARY = [
    SensorEntityDescription(
        key="power",
        native_unit_of_measurement=POWER_WATT,
        device_class=DEVICE_CLASS_POWER,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Current Consumption",
    ),
    SensorEntityDescription(
        key="energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        device_class=DEVICE_CLASS_ENERGY,
        state_class=STATE_CLASS_TOTAL_INCREASING,
        name="Total Consumption",
    )
]

SENSORS_DIAGNOSTIC = [
    SensorEntityDescription(
        key="rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=DEVICE_CLASS_SIGNAL_STRENGTH,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Signal Strength",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False
    ),
    SensorEntityDescription(
        key="batteryLevel",
        native_unit_of_measurement=PERCENTAGE,
        device_class=DEVICE_CLASS_BATTERY,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Battery Level",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="voltage",
        native_unit_of_measurement=ELECTRIC_POTENTIAL_VOLT,
        device_class=DEVICE_CLASS_VOLTAGE,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False
    ),
    SensorEntityDescription(
        key="current",
        native_unit_of_measurement=ELECTRIC_CURRENT_MILLIAMPERE,
        device_class=DEVICE_CLASS_CURRENT,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Current",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="lightLevel",
        native_unit_of_measurement=LIGHT_LUX,
        device_class=DEVICE_CLASS_ILLUMINANCE,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Illuminance",
    ),
    SensorEntityDescription(
        key="dawnTime",
        device_class=DEVICE_CLASS_TIMESTAMP,
        name="Dawn Time",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="duskTime",
        device_class=DEVICE_CLASS_TIMESTAMP,
        name="Dusk Time",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="lastEvent",
        name="Last Event Received",
        entity_category=EntityCategory.DIAGNOSTIC,
    )
]

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Find and return Lightwave sensors."""

    sensors = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]

    for featureset_id, featureset in link.featuresets.items():
        for description in SENSORS_PRIMARY:
            if featureset.has_feature(description.key):
                sensors.append(LWRF2Sensor(featureset.name, featureset_id, link, description, hass))

        for description in SENSORS_DIAGNOSTIC:
            if featureset.has_feature(description.key):
                sensors.append(LWRF2Sensor(featureset.name, featureset_id, link, description, hass))
    

    for featureset_id, hubname in link.get_hubs():
        try:
            sensors.append(LWRF2EventSensor(hubname, featureset_id, link, SensorEntityDescription(
                key="lastEvent",
                device_class=DEVICE_CLASS_TIMESTAMP,
                name="Last Event Received",
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False
            ), hass))
        except Exception as e: _LOGGER.exception("Could not add LWRF2EventSensor")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(sensors)
    async_add_entities(sensors)

class LWRF2Sensor(SensorEntity):
    """Representation of a LightwaveRF sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, name, featureset_id, link, description, hass):
        _LOGGER.debug(f"Adding sensor: {name} - {description.key} - {featureset_id}")
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = description
        
        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device
        
        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)

        self._state = None

    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)
        # The startup read is a background task and can finish after __init__
        # but before this callback is registered. Read the current value before
        # Home Assistant writes the first state.
        self._set_state(self._feature_state(self.entity_description.key))

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        # async_update is called automatically
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        state = self._featureset.features[self.entity_description.key].state
        if state is None:
            _LOGGER.debug(f"LWRF2Sensor:async_update - state is None for: {self._featureset_id} - {self.entity_description.key}")
            pass
        else:
            self._set_state(state)

    @property
    def native_value(self):
        value = self._state
        if self.entity_description.key == 'lightLevel' and value is not None:
            # Very roughly adjust the given % to Lumens using 300 lux = 100%
            lux_level = (value / 100) * RECOMMENDED_LUX_LEVEL
            value = lux_level
        
        return value

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)

    def _feature_state(self, key):
        feature = self._featureset.features.get(key)
        return None if feature is None else feature.state

    def _set_state(self, state):
        if self.entity_description.key in ("dawnTime", "duskTime"):
            converted = self._dawn_dusk_datetime(state)
            # A later read can arrive before year/month/day. Keep a timestamp
            # we already have rather than replacing it with unknown.
            if converted is None and state is not None and self._state is not None:
                return
            self._state = converted
            return

        self._state = state

    def _dawn_dusk_datetime(self, seconds):
        """Return the hub dawn or dusk instant.

        The feature is seconds since the hub's local midnight. It is combined
        with the hub year, month, and day and stored as that clock time in UTC,
        which is the real instant when the hub timezone offset is 0.
        """
        if seconds is None:
            return None

        year = self._feature_state("year")
        month = self._feature_state("month")
        day = self._feature_state("day")
        if year is None or month is None or day is None:
            return None

        try:
            seconds = int(seconds)
            start = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Could not convert %s for %s from seconds=%s year=%s month=%s day=%s",
                self.entity_description.key,
                self._featureset_id,
                seconds,
                year,
                month,
                day,
            )
            return None

        # Firmware range is 0–86400. 86400 is midnight at the end of the hub's day.
        if seconds < 0 or seconds > 86400:
            _LOGGER.debug(
                "Ignoring %s value %s for %s; expected seconds since midnight",
                self.entity_description.key,
                seconds,
                self._featureset_id,
            )
            return None

        return start + timedelta(seconds=seconds)


class LWRF2EventSensor(SensorEntity):
    """Representation of a LightwaveRF sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, name, featureset_id, link, description, hass):
        _LOGGER.debug("Adding event sensor: %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = description

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device
        
        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._state = datetime.now(pytz.utc)

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)

    async def async_added_to_hass(self):
        """Subscribe to events."""
        # This will be a noisy event, perhaps it should be a switchable option
        await self._lwlink.async_register_general_callback(self.async_update_callback)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        self._state = datetime.now(pytz.utc)

    @property
    def native_value(self):
        return self._state