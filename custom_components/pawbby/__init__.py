"""The Pawbby Smart Litter Box integration.

Standalone Home Assistant custom component (distributed via HACS) that talks
directly to the Pawbby litter box over the local network (Tuya v3.4) —
no Pawbby-Reborn server instance required.

The Tuya DP protocol was reverse-engineered in the Pawbby-Reborn project
(AGPL-3.0); see VALUES.md / DP105_SETTINGS.md there. This integration is
licensed AGPL-3.0 as well.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    CONF_DEVICE_ID,
    DOMAIN,
    DP106_COMMANDS,
)
from .coordinator import PawbbyCoordinator
from .protocol import SETTING_KEYS

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

SERVICE_ACTION = "action"
SERVICE_TARE = "tare"
SERVICE_SET_SETTING = "set_setting"

ACTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required("action"): vol.In(sorted(DP106_COMMANDS)),
    }
)
TARE_SCHEMA = vol.Schema({vol.Required(CONF_DEVICE_ID): str})
SET_SETTING_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required("setting"): vol.In(list(SETTING_KEYS)),
        vol.Required("value"): vol.Any(None, bool, int, float, str, list, dict),
    }
)


def _coordinator_for(hass: HomeAssistant, device_id: str) -> PawbbyCoordinator:
    for coordinator in hass.data.get(DOMAIN, {}).values():
        if coordinator.device_id == device_id:
            return coordinator
    raise ServiceValidationError(f"No Pawbby litter box with device_id '{device_id}'")


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_ACTION):
        return

    async def handle_action(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[CONF_DEVICE_ID])
        await coordinator.async_send_action(call.data["action"])

    async def handle_tare(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[CONF_DEVICE_ID])
        await coordinator.async_tare()

    async def handle_set_setting(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[CONF_DEVICE_ID])
        try:
            await coordinator.async_set_setting(call.data["setting"], call.data["value"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_ACTION, handle_action, schema=ACTION_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_TARE, handle_tare, schema=TARE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETTING, handle_set_setting, schema=SET_SETTING_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = PawbbyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: PawbbyCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.stop()
    return unload_ok
