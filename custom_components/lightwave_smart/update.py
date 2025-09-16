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
    make_device_info
)

DEPENDENCIES = ['lightwave_smart']
_LOGGER = logging.getLogger(__name__)

# https://developers.home-assistant.io/docs/core/entity/update/
FIRMWARE_DESCRIPTION = UpdateEntityDescription(
    key="firmware",
    device_class=UpdateDeviceClass.FIRMWARE,
    has_entity_name=True,
    entity_category=EntityCategory.DIAGNOSTIC,
)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up update entities for Lightwave Smart devices."""

    fws = []
    link = hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_LINK2]
    homekit = config_entry.options.get(CONF_HOMEKIT, False)
    
    for device_id in link.get_device_ids():
        try:
            if link.devices[device_id].is_gen2():
                fws.append(LWRF2Update(link.devices[device_id], homekit, FIRMWARE_DESCRIPTION))
        except Exception as e: _LOGGER.exception("Could not add LWRF2Update")

    hass.data[DOMAIN][config_entry.entry_id][LIGHTWAVE_ENTITIES].extend(fws)
    async_add_entities(fws)


class LWRF2Update(UpdateEntity):
    """Lightwave Firmware update entity."""

    _attr_should_poll = False
    _attr_assumed_state = False

    def __init__(self, device, homekit, entity_description):
        _LOGGER.debug(f"Adding Firmware update for device Id: {device.device_id}  name: {device.name}")
        self._device_id = device.device_id
        self._lwlink = device.link

        self.entity_description = entity_description

        self._device = self._lwlink.devices[self._device_id]
        
        self._homekit = homekit

        self._attr_unique_id = f"{self._device_id}_{self.entity_description.key}"
        
        # name = self._device.name + " Firmware"
        self._attr_device_info = make_device_info(self)
        
        self.installed_version = self._device.firmware_version
        
        self.latest_version = self._device.latest_firmware_version
        self.release_summary = self._device.latest_firmware_release_summary
        
        self.in_progress = False

    async def async_added_to_hass(self) -> None:
        """Subscribe to events."""
        await self._lwlink.async_register_firmware_event_callback(self._device_id, self.async_update_callback)
        
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
        _LOGGER.debug(f"async_update_callback - Update update: {self.entity_id} - {self.entity_description.key} - {kwargs}")
        try:
            self.latest_version = self._device.latest_firmware_version
            self.release_summary = self._device.latest_firmware_release_summary
            self.async_schedule_update_ha_state(True)
        except Exception as e: 
            _LOGGER.warning(f"async_update_callback - error - {self.entity_id} - {e}")
            
    @property
    def supported_features(self):
        """Flag supported features."""
        return UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS | UpdateEntityFeature.SPECIFIC_VERSION | UpdateEntityFeature.RELEASE_NOTES
        
    async def async_release_notes(self) -> str | None:
        """Return the release notes."""
        return self.release_summary or "None"
    
    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Install an update.

        Version can be specified to install a specific version. When `None`, the
        latest version needs to be installed.

        The backup parameter indicates a backup should be taken before
        installing the update.
        """
        _LOGGER.debug(f"async_install - {self.entity_id} - {version} - {backup}")
        
        self.in_progress = await self._device.update_firmware(version or self.latest_version)
        
