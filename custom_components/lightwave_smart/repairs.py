"""Repairs for Lightwave Smart."""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


class RemoveDuplicateEntryFlow(ConfirmRepairFlow):
    """Remove a config entry that repeats a login already set up."""

    def __init__(self, entry_id: str, owner_entry_id: str | None) -> None:
        self._entry_id = entry_id
        self._owner_entry_id = owner_entry_id

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            if self.hass.config_entries.async_get_entry(self._entry_id) is not None:
                await self.hass.config_entries.async_remove(self._entry_id)
            # The removed entry may have held unique ids the owner also uses.
            owner = (
                self.hass.config_entries.async_get_entry(self._owner_entry_id)
                if self._owner_entry_id
                else None
            )
            if owner is not None and owner.state is ConfigEntryState.LOADED:
                self.hass.config_entries.async_schedule_reload(owner.entry_id)
            return self.async_create_entry(data={})
        return await super().async_step_confirm()


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict | None
) -> RepairsFlow:
    data = data or {}
    entry_id = data.get("entry_id")
    if issue_id.endswith("_duplicate_account") and entry_id:
        return RemoveDuplicateEntryFlow(entry_id, data.get("owner_entry_id"))
    return ConfirmRepairFlow()
