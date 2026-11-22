"""Sensors for the Pawbby litter box and per-cat stats (mirrors mqtt-bridge.ts SENSORS)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PawbbyCoordinator
from .entity import PawbbyCatEntity, PawbbyEntity


def _ts(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


@dataclass(frozen=True, kw_only=True)
class PawbbySensorDescription(SensorEntityDescription):
    value_fn: Callable[[PawbbyCoordinator], Any]


SENSORS: tuple[PawbbySensorDescription, ...] = (
    PawbbySensorDescription(
        key="status",
        name="Status",
        icon="mdi:information-outline",
        value_fn=lambda c: c.data.get("status"),
    ),
    PawbbySensorDescription(
        key="waste_bin",
        name="Waste Bin",
        icon="mdi:delete",
        value_fn=lambda c: c.data.get("waste_bin"),
    ),
    PawbbySensorDescription(
        key="litter_level",
        name="Litter Level",
        icon="mdi:dots-grid",
        value_fn=lambda c: c.data.get("litter_level"),
    ),
    PawbbySensorDescription(
        key="visits_today",
        name="Visits Today",
        icon="mdi:cat",
        native_unit_of_measurement="visits",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.data.get("visits_today"),
    ),
    PawbbySensorDescription(
        key="last_visit_weight",
        name="Last Visit Weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement="kg",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: (c.data.get("last_visit") or None)
        and c.data["last_visit"].weight_kg,
    ),
    PawbbySensorDescription(
        key="last_visit_pet",
        name="Last Visit Pet",
        icon="mdi:paw",
        value_fn=lambda c: next(
            (
                cat.name
                for cat in c.state_machine.cats
                if (c.data.get("last_visit") and c.data["last_visit"].pet_id == cat.id)
            ),
            None,
        ),
    ),
    PawbbySensorDescription(
        key="last_visit_at",
        name="Last Visit",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _ts(c.data["last_visit"].timestamp if c.data.get("last_visit") else None),
    ),
    PawbbySensorDescription(
        key="last_cleaned",
        name="Last Cleaned",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c: _ts(
            ev.timestamp
            if (
                ev := c.state_machine.last_event_of(
                    "auto-clean", "manual-clean", "manual-clean-button", "clean-completed"
                )
            )
            else None
        ),
    ),
    PawbbySensorDescription(
        key="deodorizer_days_left",
        name="Deodorizer Days Left",
        icon="mdi:air-filter",
        native_unit_of_measurement="days",
        value_fn=lambda c: c.data.get("settings").deodorant_days if c.data.get("settings") else None,
    ),
)


@dataclass(frozen=True, kw_only=True)
class PawbbyCatSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


CAT_SENSORS: tuple[PawbbyCatSensorDescription, ...] = (
    PawbbyCatSensorDescription(
        key="weight",
        name="Weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement="kg",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["latest_weight_kg"],
    ),
    PawbbyCatSensorDescription(
        key="last_used",
        name="Last Used",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: _ts(s["last_used_at"]),
    ),
    PawbbyCatSensorDescription(
        key="last_duration",
        name="Last Duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s["last_duration_s"],
    ),
    PawbbyCatSensorDescription(
        key="visits_today",
        name="Visits Today",
        icon="mdi:paw",
        native_unit_of_measurement="visits",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s["visits_today"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    entities: list[SensorEntity] = [
        PawbbySensor(coordinator, description) for description in SENSORS
    ]
    for cat in coordinator.state_machine.cats:
        for description in CAT_SENSORS:
            entities.append(PawbbyCatSensor(coordinator, cat, description))
    async_add_entities(entities)


class PawbbySensor(PawbbyEntity, SensorEntity):
    entity_description: PawbbySensorDescription

    def __init__(self, coordinator: PawbbyCoordinator, description: PawbbySensorDescription) -> None:
        super().__init__(coordinator, f"sensor_{description.key}")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)


class PawbbyCatSensor(PawbbyCatEntity, SensorEntity):
    entity_description: PawbbyCatSensorDescription

    def __init__(
        self,
        coordinator: PawbbyCoordinator,
        cat: Any,
        description: PawbbyCatSensorDescription,
    ) -> None:
        super().__init__(coordinator, cat.id, cat.name, f"sensor_{description.key}")
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.cat_stats())
