"""Config switches for the hardware settings stored on the box (DP 105).

State is read from the latest DP 103 snapshot; commands go back via
``coordinator.async_set_setting`` (mirrors mqtt-bridge.ts SETTING_SWITCHES).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PawbbyCoordinator
from .entity import PawbbyEntity


@dataclass(frozen=True, kw_only=True)
class PawbbySwitchDescription(SwitchEntityDescription):
    setting_key: str
    settings_attr: str
    icon: str


SWITCHES: tuple[PawbbySwitchDescription, ...] = (
    PawbbySwitchDescription(
        key="auto_clean", name="Auto-Clean", setting_key="auto_clean",
        settings_attr="auto_clean", icon="mdi:robot-vacuum",
    ),
    PawbbySwitchDescription(
        key="sleep_mode", name="Quiet Period", setting_key="sleep_mode",
        settings_attr="sleep_enabled", icon="mdi:sleep",
    ),
    PawbbySwitchDescription(
        key="soft_clumps", name="Soft Clumps Mode", setting_key="soft_clumps",
        settings_attr="soft_clumps", icon="mdi:shaker-outline",
    ),
    PawbbySwitchDescription(
        key="auto_off_screen", name="Auto Screen-Off", setting_key="auto_off_screen",
        settings_attr="auto_off_screen", icon="mdi:monitor-off",
    ),
    PawbbySwitchDescription(
        key="child_lock", name="Screen Lock", setting_key="child_lock",
        settings_attr="child_lock", icon="mdi:lock-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PawbbyCoordinator = hass.data[entry.domain][entry.entry_id]
    async_add_entities(
        PawbbySwitch(coordinator, description) for description in SWITCHES
    )


class PawbbySwitch(PawbbyEntity, SwitchEntity):
    entity_description: PawbbySwitchDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PawbbyCoordinator, description: PawbbySwitchDescription) -> None:
        super().__init__(coordinator, f"switch_{description.key}")
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        settings = self.coordinator.data.get("settings")
        if settings is None:
            return None
        return getattr(settings, self.entity_description.settings_attr)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting(self.entity_description.setting_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting(self.entity_description.setting_key, False)
