"""Buttons: motor commands + tare (mirrors mqtt-bridge.ts BUTTONS)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


@dataclass(frozen=True, kw_only=True)
class PawbbyButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[PawbbyCoordinator], Awaitable[None]]


BUTTONS: tuple[PawbbyButtonDescription, ...] = (
    PawbbyButtonDescription(
        key="clean", name="Clean", icon="mdi:broom",
        press_fn=lambda c: c.async_send_action("clean"),
    ),
    PawbbyButtonDescription(
        key="flatten", name="Flatten", icon="mdi:road-variant",
        press_fn=lambda c: c.async_send_action("flatten"),
    ),
    PawbbyButtonDescription(
        key="empty", name="Empty", icon="mdi:delete-empty",
        press_fn=lambda c: c.async_send_action("empty"),
    ),
    PawbbyButtonDescription(
        key="tare", name="Zero Scale", icon="mdi:scale",
        press_fn=lambda c: c.async_tare(),
    ),
    PawbbyButtonDescription(
        key="cancel_clean", name="Cancel Clean", icon="mdi:stop-circle",
        press_fn=lambda c: c.async_send_action("cancel_clean"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities(PawbbyButton(coordinator, d) for d in BUTTONS)


class PawbbyButton(PawbbyEntity, ButtonEntity):
    entity_description: PawbbyButtonDescription

    def __init__(self, coordinator: PawbbyCoordinator, description: PawbbyButtonDescription) -> None:
        super().__init__(coordinator, f"button_{description.key}")
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
