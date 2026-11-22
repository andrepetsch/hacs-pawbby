"""DataUpdateCoordinator wiring the Tuya client and the state machine together.

The box is push-driven (local Tuya), so the coordinator's poll is only a
fallback: real updates arrive via ``PawbbyTuyaClient.on_data`` →
``StateMachine.handle_dps`` → HA event bus + ``async_set_updated_data``.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    BUSY_STATES,
    CONF_CATS,
    CONF_DEVICE_ID,
    CONF_IP,
    CONF_LOCAL_KEY,
    CONF_SCAN_INTERVAL,
    CONF_WEIGHT_UNIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_PAWBBY,
    SPLIT_VISIT_WINDOW_S,
)
from .pawid import CatProfile
from .pawbby_client import ClientNotConnectedError, PawbbyTuyaClient
from .protocol import build_setting_command
from .state_machine import StateMachine
import logging

_LOGGER = logging.getLogger(__name__)


class PawbbyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates one Pawbby litter box (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.entry = entry
        weight_unit = entry.options.get(CONF_WEIGHT_UNIT, "kg")
        cats = [
            CatProfile(id=c["id"], name=c["name"], weight_kg=float(c["weight_kg"]))
            for c in entry.data.get(CONF_CATS, [])
        ]
        self.state_machine = StateMachine(cats, desired_weight_unit=weight_unit)
        self.client = PawbbyTuyaClient(
            device_id=entry.data[CONF_DEVICE_ID],
            local_key=entry.data[CONF_LOCAL_KEY],
            ip=entry.data.get(CONF_IP),
            timezone_name=hass.config.time_zone or "UTC",
            weight_unit=weight_unit,
            on_data=self._on_message,
            on_connected=self._on_connection_change,
            on_disconnected=self._on_connection_change,
        )

    # ------------------------------------------------------------- properties

    @property
    def device_id(self) -> str:
        return self.entry.data[CONF_DEVICE_ID]

    @property
    def box_name(self) -> str:
        return f"Pawbby {self.device_id[-6:]}"

    # ------------------------------------------------------------ HA lifecycle

    async def _async_setup(self) -> dict[str, Any]:
        await self.client.start()
        return self.state_machine.snapshot()

    async def _async_update_data(self) -> dict[str, Any]:
        # Fallback poll only — real updates are push-driven via _on_message.
        return self.state_machine.snapshot()

    # ------------------------------------------------------------ client hooks

    async def _on_message(self, msg: dict[str, Any]) -> None:
        """Called by the client in the event loop for every raw Tuya message."""
        dps = msg.get("dps") or {}
        events, commands = self.state_machine.handle_dps(dps)
        for ev in events:
            self.hass.bus.async_fire(
                EVENT_PAWBBY,
                {
                    "entry_id": self.entry.entry_id,
                    "device_id": self.device_id,
                    "type": ev.type,
                    "pet_id": ev.pet_id,
                    "weight_kg": ev.weight_kg,
                    "duration_s": ev.duration_s,
                    "timestamp": ev.timestamp,
                },
            )
        for cmd in commands:  # e.g. weight-unit sync requested by the state machine
            try:
                await self.client.send_setting(cmd)
            except ClientNotConnectedError:
                _LOGGER.debug("Skipping %s: box not connected", cmd.key)
        self.async_set_updated_data(self.state_machine.snapshot())

    def _on_connection_change(self, *args: Any) -> None:
        self.async_set_updated_data(self.state_machine.snapshot())

    # ---------------------------------------------------------------- commands

    def _check_writable(self) -> None:
        """Busy/settle guards ported from tuya-listener's tuya:action hook."""
        st = self.state_machine.state
        if not self.client.is_connected:
            raise HomeAssistantError(f"Pawbby box {self.device_id} is not connected")
        if st.status_raw in BUSY_STATES:
            raise HomeAssistantError(
                f"Litter box is busy ({st.status_raw}); command refused"
            )
        if (
            st.last_visit_ended_at is not None
            and time.time() - st.last_visit_ended_at < SPLIT_VISIT_WINDOW_S
        ):
            raise HomeAssistantError("A cat just left the box; command refused")

    async def async_send_action(self, action: str) -> None:
        """Motor command (clean/flatten/empty/cancel_clean) with busy guards."""
        if action != "cancel_clean":
            self._check_writable()
        self.state_machine.mark_app_action()
        await self.client.send_action(action)

    async def async_tare(self) -> None:
        self._check_writable()
        await self.client.tare()

    async def async_set_setting(self, key: str, value: Any) -> None:
        """Change a hardware setting stored on the box (DP 105)."""
        if key == "refresh":
            await self.client.refresh_status()
            return
        cmd = build_setting_command(key, value)
        await self.client.send_setting(cmd)
        self.hass.bus.async_fire(
            EVENT_PAWBBY,
            {
                "entry_id": self.entry.entry_id,
                "device_id": self.device_id,
                "type": "settings-changed",
                "setting": cmd.key,
                "description": cmd.description,
                "timestamp": time.time(),
            },
        )
        self.async_set_updated_data(self.state_machine.snapshot())
