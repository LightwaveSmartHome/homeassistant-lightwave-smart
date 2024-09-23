import logging
from .const import LIGHTWAVE_LINK2, LIGHTWAVE_ENTITIES, SERVICE_SETBRIGHTNESS, CONF_HOMEKIT, DOMAIN
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity, 
    UpdateEntityDescription,
    UpdateEntityFeature
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .utils import (
    make_device_info,
    get_extra_state_attributes
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

# https://developers.home-assistant.io/docs/core/entity/update/
FIRMWARE_DESCRIPTION = UpdateEntityDescription(
    key="firmware",
    name="??",
    device_class=UpdateDeviceClass.FIRMWARE,
    # translation_key="button",
    has_entity_name=True,
    
    # title="",
    
    entity_category=EntityCategory.DIAGNOSTIC,
)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up update entities for Lightwave Smart devices."""

    fws = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]
    homekit = config_entry.options.get(CONF_HOMEKIT, False)
    
    #  TODO - get smart devices - FM updatable, add update entities for them
    #   how to make sub-entities of those?  DIAGNOSTIC ? 
    #   same uniqueId?!??
    
    for device_id in link.get_device_ids():
        try:
            if link.devices[device_id].is_gen2():
                # for featureset_id, name in link.get_lights():
                fws.append(LWRF2Update(device_id, link, homekit, FIRMWARE_DESCRIPTION))
        except Exception as e: _LOGGER.exception("Could not add LWRF2Update")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(fws)
    async_add_entities(fws)


class LWRF2Update(UpdateEntity):
    """Lightwave Firmware update entity."""

    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, device_id, link, homekit, entity_description):
        # _LOGGER.warning("UPDATE ###########################################################################")
        
        _LOGGER.debug("Adding Firmware update for device Id: %s ", device_id)
        self._device_id = device_id
        self._lwlink = link

        # for hub_featureset_id, hubname in self._lwlink.get_hubs():
            # self._linkid = hub_featureset_id

        self.entity_description = entity_description

        self._homekit = homekit

        self._attr_unique_id = f"{self._device_id}_{self.entity_description.key}"
        # self._attr_device_info = make_device_info(self, name)
        
        self.device = self._lwlink.devices[self._device_id]
        # self.installed_version = self.device.firmware_version
        self.installed_version = 1
        
        # TODO - get latest version from WS API
        
        # _LOGGER.warning("UPDATE #####@ %s", dir(feature_set))
        
        self.latest_version = 2
        self.release_summary = self.device.latest_firmware_release_summary
        
        _LOGGER.warning("UPDATE ############################# ? ##@ %s", self.latest_version)
        

    async def async_added_to_hass(self) -> None:
        """Subscribe to events."""
        await self._lwlink.async_register_device_callback(self._device_id, self.async_update_callback)
        
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        if self._homekit:
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
            _LOGGER.debug("async_update_callback - Update update: %s - %s - %s", self.entity_id, kwargs, self.entity_description.key)
            _LOGGER.warning(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> ?1 async_update_callback - Update update: %s - %s - %s", self.entity_id, kwargs, self.entity_description.key)
            self.latest_version = self.device.latest_firmware_version
            self.release_summary = self.device.latest_firmware_release_summary
            _LOGGER.warning(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> ?2 async_update_callback - latest_version: %s ", self.latest_version)
            
        except Exception as e: 
            _LOGGER.warning("async_update_callback - err %s - %s ", self.entity_id, e)
        
    @property
    def supported_features(self):
        """Flag supported features."""
        return UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.SPECIFIC_VERSION | UpdateEntityFeature.RELEASE_NOTES
    
    
    
    # TODO
    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Install an update.

        Version can be specified to install a specific version. When `None`, the
        latest version needs to be installed.

        The backup parameter indicates a backup should be taken before
        installing the update.
        """
        
    async def async_release_notes(self) -> str | None:
        """Return the release notes."""
        return "Lorem ipsum"