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
    LIGHT_LUX = SensorDeviceClass.ILLUMINANCE
except ImportError:
    from homeassistant.components.sensor import (
        DEVICE_CLASS_BATTERY, 
        DEVICE_CLASS_CURRENT, 
        DEVICE_CLASS_ENERGY, 
        DEVICE_CLASS_ILLUMINANCE, 
        DEVICE_CLASS_POWER, 
        DEVICE_CLASS_SIGNAL_STRENGTH, 
        DEVICE_CLASS_TIMESTAMP, 
        DEVICE_CLASS_VOLTAGE,
        LIGHT_LUX
    )
# Units
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
try:
    from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfPower
    ELECTRIC_CURRENT_MILLIAMPERE = UnitOfElectricCurrent.MILLIAMPERE
    ELECTRIC_POTENTIAL_VOLT = UnitOfElectricPotential.VOLT
    ENERGY_WATT_HOUR = UnitOfEnergy.WATT_HOUR
    POWER_WATT = UnitOfPower.WATT
except ImportError:
    from homeassistant.const import (POWER_WATT, ENERGY_WATT_HOUR, ELECTRIC_POTENTIAL_VOLT, ELECTRIC_CURRENT_MILLIAMPERE)

from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import EntityCategory
from homeassistant.exceptions import ConfigEntryNotReady
from datetime import datetime
import pytz
from .utils import (
    make_device_info,
    get_extra_state_attributes
)

RECOMMENDED_LUX_LEVEL = 300

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

SENSORS = [
    SensorEntityDescription(
        key="power",
        native_unit_of_measurement=POWER_WATT,
        device_class=DEVICE_CLASS_POWER,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Current Consumption",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="energy",
        native_unit_of_measurement=ENERGY_WATT_HOUR,
        device_class=DEVICE_CLASS_ENERGY,
        state_class=STATE_CLASS_TOTAL_INCREASING,
        name="Total Consumption",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=DEVICE_CLASS_SIGNAL_STRENGTH,
        state_class=STATE_CLASS_MEASUREMENT,
        name="Signal Strength",
        entity_category=EntityCategory.DIAGNOSTIC,
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
        for description in SENSORS:
            if featureset.has_feature(description.key):
                sensors.append(LWRF2Sensor(featureset.name, featureset_id, link, description, hass))
    
    for featureset_id, hubname in link.get_hubs():
            try:
                sensors.append(LWRF2EventSensor(hubname, featureset_id, link, SensorEntityDescription(
                    key="lastEvent",
                    device_class=DEVICE_CLASS_TIMESTAMP,
                    name="Last Event Received",
                    entity_category=EntityCategory.DIAGNOSTIC,
                )))
            except Exception as e: _LOGGER.exception("Could not add LWRF2EventSensor")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(sensors)
    async_add_entities(sensors)

class LWRF2Sensor(SensorEntity):
    """Representation of a LightwaveRF sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, name, featureset_id, link, description, hass):
        _LOGGER.debug("Adding sensor: %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        for hub_featureset_id, hubname in self._lwlink.get_hubs():
            self._linkid = hub_featureset_id
        
        self.entity_description = description

        self._state = self._lwlink.featuresets[self._featureset_id].features[self.entity_description.key].state
        if self._state is None:
            _LOGGER.warning("LWRF2Sensor:__init__ - state is None for: %s - %s", self._featureset_id, self.entity_description.key)
        else:
            if self.entity_description.key == 'duskTime' or self.entity_description.key == 'dawnTime':
                year = self._lwlink.featuresets[self._featureset_id].features['year'].state
                month = self._lwlink.featuresets[self._featureset_id].features['month'].state
                day = self._lwlink.featuresets[self._featureset_id].features['day'].state
                hour = self._state // 3600
                self._state = self._state - hour * 3600
                min = self._state // 60
                second = self._state - min * 60
                self._state = dt_util.parse_datetime(f'{year}-{month:02}-{day:02}T{hour:02}:{min:02}:{second:02}Z')

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_device_info(self, name)

    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        if kwargs["feature"] == "buttonPress":
            _LOGGER.debug("Button (light) press event: %s %s", self.entity_id, kwargs["new_value"])
            self.hass.bus.fire("lightwave_smart.click",{"entity_id": self.entity_id, "code": kwargs["new_value"]},
        )
        self.async_schedule_update_ha_state(True)

    async def async_update(self):
        """Update state"""
        self._state = self._lwlink.featuresets[self._featureset_id].features[self.entity_description.key].state
        if self._state is None:
            _LOGGER.warning("LWRF2Sensor:async_update - state is None for: %s - %s", self._featureset_id, self.entity_description.key)
        else:
            if self.entity_description.key == 'duskTime' or self.entity_description.key == 'dawnTime':
                year = self._lwlink.featuresets[self._featureset_id].features['year'].state
                month = self._lwlink.featuresets[self._featureset_id].features['month'].state
                day = self._lwlink.featuresets[self._featureset_id].features['day'].state
                hour = self._state // 3600
                self._state = self._state - hour * 3600
                min = self._state // 60
                second = self._state - min * 60
                self._state = dt_util.parse_datetime(f'{year}-{month:02}-{day:02}T{hour:02}:{min:02}:{second:02}Z')

    @property
    def native_value(self):
        value = self._state
        if self.entity_description.key == 'lightLevel':
            # Very roughly adjust the given % to Lumens using 300 lux = 100%
            lux_level = (value / 100) * RECOMMENDED_LUX_LEVEL
            value = lux_level
        
        return value

    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)


class LWRF2EventSensor(SensorEntity):
    """Representation of a LightwaveRF sensor."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, name, featureset_id, link, description):
        _LOGGER.debug("Adding event sensor: %s - %s - %s ", name, description.key, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link
        self._linkid = featureset_id
        
        self.entity_description = description

        self._state = datetime.now(pytz.utc)

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_device_info(self, name)

    async def async_added_to_hass(self):
        """Subscribe to events."""
        await self._lwlink.async_register_feature_callback(self._featureset_id, self.async_update_callback)

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