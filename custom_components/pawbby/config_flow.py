"""Config flow for the Pawbby Smart Litter Box integration.

Steps: user (Tuya credentials + optional IP, validated with a live connection
test) -> cats (up to 4 cats with profile weights for PawID) -> entry created.
Options flow: fallback scan interval, weight unit, edit cats.
Reauth: new local key when the box rejects the old one.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONF_CATS,
    CONF_DEVICE_ID,
    CONF_IP,
    CONF_LOCAL_KEY,
    CONF_SCAN_INTERVAL,
    CONF_WEIGHT_UNIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_CATS,
    TUYA_PROTOCOL_VERSION,
)
from .pawbby_client import _default_device_factory

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required(CONF_LOCAL_KEY): str,
        vol.Optional(CONF_IP): str,
    }
)


def _test_connection(device_id: str, local_key: str, ip: str | None) -> None:
    """Blocking live check (runs in the executor): build device + read status."""
    device = _default_device_factory(device_id, ip, local_key, TUYA_PROTOCOL_VERSION)
    try:
        device.status()
    finally:
        device.close()


def _cats_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    schema: dict[vol.Optional, Any] = {}
    for i in range(1, MAX_CATS + 1):
        schema[vol.Optional(f"cat_{i}_name", default=defaults.get(f"cat_{i}_name", ""))] = str
        schema[
            vol.Optional(f"cat_{i}_weight", default=defaults.get(f"cat_{i}_weight", 4.0))
        ] = vol.Coerce(float)
    return vol.Schema(schema)


def _cats_from_input(user_input: dict[str, Any]) -> list[dict[str, Any]]:
    cats = []
    for i in range(1, MAX_CATS + 1):
        name = str(user_input.get(f"cat_{i}_name") or "").strip()
        if not name:
            continue
        cats.append(
            {"id": f"cat_{i}", "name": name, "weight_kg": float(user_input[f"cat_{i}_weight"])}
        )
    return cats


class PawbbyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._device_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured()
            try:
                await self.hass.async_add_executor_job(
                    _test_connection,
                    user_input[CONF_DEVICE_ID],
                    user_input[CONF_LOCAL_KEY],
                    user_input.get(CONF_IP),
                )
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                self._device_data = dict(user_input)
                return await self.async_step_cats()
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_cats(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            for i in range(1, MAX_CATS + 1):
                name = str(user_input.get(f"cat_{i}_name") or "").strip()
                if name and float(user_input.get(f"cat_{i}_weight") or 0) <= 0:
                    errors[f"cat_{i}_weight"] = "invalid_weight"
            if not errors:
                data = {**self._device_data, CONF_CATS: _cats_from_input(user_input)}
                return self.async_create_entry(
                    title=f"Pawbby ({data[CONF_DEVICE_ID][-6:]})", data=data
                )
        return self.async_show_form(
            step_id="cats", data_schema=_cats_schema(), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._device_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    _test_connection,
                    self._device_data[CONF_DEVICE_ID],
                    user_input[CONF_LOCAL_KEY],
                    self._device_data.get(CONF_IP),
                )
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                assert entry is not None
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_LOCAL_KEY: user_input[CONF_LOCAL_KEY]}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_LOCAL_KEY): str}),
            errors=errors,
            description_placeholders={CONF_DEVICE_ID: self._device_data.get(CONF_DEVICE_ID, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "PawbbyOptionsFlow":
        return PawbbyOptionsFlow()


class PawbbyOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            cats = _cats_from_input(user_input)
            # keep cat ids stable across option edits: reuse stored cats by name
            stored = {c["name"]: c["id"] for c in self.config_entry.data.get(CONF_CATS, [])}
            for cat in cats:
                cat["id"] = stored.get(cat["name"], cat["id"])
            options = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_WEIGHT_UNIT: user_input[CONF_WEIGHT_UNIT],
            }
            return self.async_create_entry(data={**options, CONF_CATS: cats})
        entry = self.config_entry
        cats_defaults: dict[str, Any] = {}
        for i, cat in enumerate(entry.data.get(CONF_CATS, [])[:MAX_CATS], start=1):
            cats_defaults[f"cat_{i}_name"] = cat["name"]
            cats_defaults[f"cat_{i}_weight"] = cat["weight_kg"]
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_WEIGHT_UNIT, default=entry.options.get(CONF_WEIGHT_UNIT, "kg")
                ): vol.In(["kg", "lb"]),
            }
        )
        # merge cat fields into the same schema dict
        schema.schema.update(_cats_schema(cats_defaults).schema)
        return self.async_show_form(step_id="init", data_schema=schema)
