import logging
import traceback
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, SERVICE_SETBRIGHTNESS, CONF_HOMEKIT, DOMAIN
from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
    EventEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .utils import (
    make_entity_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

ATTRIBUTES = [ None, "Up", "Down" ]
TYPES = [ "Short", "Long", "Long-Release" ]
PRESSES_MAX = 5

EVENT_TYPES_BUTTON = [ None, 'Short.1', 'Short.2', 'Short.3', 'Short.4', 'Short.5', 'Long', 'Long-Release' ]
EVENT_TYPES_BUTTON_PAIR = [ None, 'Up.Short.1', 'Up.Short.2', 'Up.Short.3', 'Up.Short.4', 'Up.Short.5', 'Up.Long', 
                           'Up.Long-Release', 'Down.Short.1', 'Down.Short.2', 'Down.Short.3', 'Down.Short.4', 'Down.Short.5', 'Down.Long', 'Down.Long-Release' ]
            
SMART_SWITCH = EventEntityDescription(
    key="uiButton",
    name="Smart Switch",
    device_class=EventDeviceClass.BUTTON,
    event_types=EVENT_TYPES_BUTTON,
    # translation_key="button",
    has_entity_name=True,
)

SMART_SWITCH_PAIR = EventEntityDescription(
    key="uiButtonPair",
    name="Smart Switch Pair",
    device_class=EventDeviceClass.BUTTON,
    event_types=EVENT_TYPES_BUTTON_PAIR,
    # translation_key="button",
    has_entity_name=True,
)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Find and return Lightwave uibuttons."""

    uibuttons = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]
    homekit = config_entry.options.get(CONF_HOMEKIT, False)
                
    for featureset_id, name in link.get_uiButtonPair_producers():
        try:
            uibuttons.append(LWRF2UIButton(name, featureset_id, link, homekit, SMART_SWITCH_PAIR))
        except Exception as e: _LOGGER.exception("Could not add LWRF2UIButton - Pair")

    for featureset_id, name in link.get_uiButton_producers():
        try:
            uibuttons.append(LWRF2UIButton(name, featureset_id, link, homekit, SMART_SWITCH))
        except Exception as e: _LOGGER.exception("Could not add LWRF2UIButton")
        

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(uibuttons)
    async_add_entities(uibuttons)


class LWRF2UIButton(EventEntity):
    """Representation of a Lightwave uibutton."""

    _attr_should_poll = False

    def __init__(self, name, featureset_id, link, homekit, entity_description):
        _LOGGER.debug("Adding uibutton: %s - %s ", name, featureset_id)
        self._featureset_id = featureset_id
        self._lwlink = link

        self.entity_description = entity_description

        self._featureset = self._lwlink.featuresets[self._featureset_id]
        self._device = self._featureset.device

        self._homekit = homekit

        self._gen2 = self._featureset.is_gen2()
        self._attr_assumed_state = not self._gen2

        self._attr_unique_id = f"{self._featureset_id}_{self.entity_description.key}"
        self._attr_device_info = make_entity_device_info(self, name)

        self._state = None

    async def async_added_to_hass(self) -> None:
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

    @callback
    def async_update_callback(self, **kwargs):
        """Update the component's state."""
        try:
            _LOGGER.debug(f"async_update_callback - Button event: {self.entity_id} - {kwargs} - {self.entity_description.key}")
            
            if kwargs["feature"] == self.entity_description.key:
                feature = self._lwlink.get_feature_by_featureid(kwargs["feature_id"])
                if feature.decoded_obj is not None: 
                    self._state = self._get_event_type(feature.decoded_obj)
                    self._trigger_event(self._state)
                    self.async_schedule_update_ha_state(True)
                
        except Exception as e: 
            _LOGGER.warning(f"async_update_callback - error - entity: {self.entity_id} - {e} - kwargs: {kwargs} - error stack: {traceback.format_exc()}")
            pass
        
        
    def _get_event_type(self, decoded_obj):
        event_type = ""
        if "upDown" in decoded_obj:
            event_type = decoded_obj["upDown"] + "."
        
        event_type += decoded_obj['eventType']
        if decoded_obj['eventType'] == "Short":
            event_type += "." + str(decoded_obj['presses'])
        
        return event_type
        
    @property
    def extra_state_attributes(self):
        """Return the optional state attributes."""
        return get_extra_state_attributes(self)

