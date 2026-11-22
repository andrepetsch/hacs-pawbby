"""Shared base entity for the Pawbby integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PawbbyCoordinator


class PawbbyEntity(CoordinatorEntity[PawbbyCoordinator]):
    """Entity attached to the litter-box device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PawbbyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.device_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            name=self.coordinator.box_name,
            manufacturer="Pawbby Reborn",
            model="Smart Litter Box",
        )


class PawbbyCatEntity(CoordinatorEntity[PawbbyCoordinator]):
    """Entity attached to a cat device (visits are global, cats are not box-scoped)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PawbbyCoordinator, cat_id: str, cat_name: str, key: str) -> None:
        super().__init__(coordinator)
        self._cat_id = cat_id
        self._key = key
        self._attr_unique_id = f"pet_{cat_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"pet_{cat_id}")},
            name=cat_name,
            manufacturer="Pawbby Reborn",
            model="Cat",
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self._attr_device_info

    def cat_stats(self) -> dict[str, Any]:
        return self.coordinator.state_machine.cat_stats(self._cat_id)
