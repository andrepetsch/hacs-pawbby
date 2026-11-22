"""Number entity: auto-clean delay (1-60 min, DP 105 gate 6)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities([PawbbyAutoCleanDelayNumber(coordinator)])


class PawbbyAutoCleanDelayNumber(PawbbyEntity, NumberEntity):
    _attr_name = "Auto-Clean Delay"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-outline"
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: PawbbyCoordinator) -> None:
        super().__init__(coordinator, "number_auto_clean_delay")

    @property
    def native_value(self) -> float | None:
        settings = self.coordinator.data.get("settings")
        if settings is None:
            return None
        return float(settings.auto_clean_delay_min)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting("auto_clean_delay", int(value))
