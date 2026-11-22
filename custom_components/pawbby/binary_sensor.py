"""Binary sensors (mirrors mqtt-bridge.ts BINARY)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


@dataclass(frozen=True, kw_only=True)
class PawbbyBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PawbbyCoordinator], bool | None]


BINARY_SENSORS: tuple[PawbbyBinarySensorDescription, ...] = (
    PawbbyBinarySensorDescription(
        key="online",
        name="Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda c: c.client.is_connected,
    ),
    PawbbyBinarySensorDescription(
        key="cleaning",
        name="Cleaning",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: c.data.get("cleaning"),
    ),
    PawbbyBinarySensorDescription(
        key="lid_open",
        name="Lid Open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda c: c.data.get("lid_open"),
    ),
    PawbbyBinarySensorDescription(
        key="bin_full",
        name="Waste Bin Full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.data.get("bin_full"),
    ),
    PawbbyBinarySensorDescription(
        key="bin_removed",
        name="Bin Removed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.data.get("bin_removed"),
    ),
    PawbbyBinarySensorDescription(
        key="drum_removed",
        name="Drum Removed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.data.get("drum_removed"),
    ),
    PawbbyBinarySensorDescription(
        key="litter_low",
        name="Litter Low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.data.get("litter_low"),
    ),
    PawbbyBinarySensorDescription(
        key="motor_error",
        name="Motor Error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: c.data.get("motor_error"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities(
        PawbbyBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class PawbbyBinarySensor(PawbbyEntity, BinarySensorEntity):
    entity_description: PawbbyBinarySensorDescription

    def __init__(
        self, coordinator: PawbbyCoordinator, description: PawbbyBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, f"binary_{description.key}")
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator)
