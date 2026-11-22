"""Select entity: litter type (DP 105 gate 5)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import LITTER_TYPES
from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities([PawbbyLitterTypeSelect(coordinator)])


class PawbbyLitterTypeSelect(PawbbyEntity, SelectEntity):
    _attr_name = "Litter Type"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:grain"
    _attr_options = [t["name"] for t in LITTER_TYPES]

    def __init__(self, coordinator: PawbbyCoordinator) -> None:
        super().__init__(coordinator, "select_litter_type")

    @property
    def current_option(self) -> str | None:
        settings = self.coordinator.data.get("settings")
        if settings is None:
            return None
        for t in LITTER_TYPES:
            if t["id"] == settings.litter_type:
                return t["name"]
        return None

    async def async_select_option(self, option: str) -> None:
        for t in LITTER_TYPES:
            if t["name"] == option:
                await self.coordinator.async_set_setting("litter_type", t["id"])
                return
        raise ValueError(f"Unknown litter type option: {option}")
