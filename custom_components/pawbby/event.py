"""Event entity: surfaces every litter-box event as an HA event entity
(mirrors the MQTT 'event' entity in mqtt-bridge.ts)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EVENT_PAWBBY, EVENT_TYPES
from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities([PawbbyEventEntity(coordinator)])


class PawbbyEventEntity(PawbbyEntity, EventEntity):
    _attr_name = "Event"
    _attr_event_types = list(EVENT_TYPES)

    def __init__(self, coordinator: PawbbyCoordinator) -> None:
        super().__init__(coordinator, "event")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_PAWBBY, self._handle_bus_event)
        )

    @callback
    def _handle_bus_event(self, event: Event) -> None:
        data: dict[str, Any] = event.data
        if data.get("device_id") != self.coordinator.device_id:
            return
        event_type = data.get("type")
        if event_type not in self._attr_event_types:
            return
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
