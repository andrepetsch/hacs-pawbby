"""HA-level tests: config flow, entry setup, coordinator wiring, services.

Uses pytest-homeassistant-custom-component (real HomeAssistant test harness)
with the Tuya client replaced by a fake.
"""

from __future__ import annotations

import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.pawbby.const import (
    CONF_CATS,
    CONF_DEVICE_ID,
    CONF_IP,
    CONF_LOCAL_KEY,
    DOMAIN,
    EVENT_PAWBBY,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_connected = True
        self.started = False
        self.sent_actions: list[str] = []
        self.sent_settings: list = []
        self.tare_calls = 0
        FakeClient.instances.append(self)

    @property
    def device_id(self):
        return self.kwargs["device_id"]

    async def start(self):
        self.started = True

    async def stop(self):
        self.started = False
        self.is_connected = False

    async def send_action(self, action):
        self.sent_actions.append(action)

    async def send_setting(self, cmd):
        self.sent_settings.append(cmd)

    async def tare(self):
        self.tare_calls += 1

    async def refresh_status(self):
        pass


def _patch_connect(monkeypatch, fail=False):
    def fake(device_id, local_key, ip):
        if fail:
            raise ConnectionError("unreachable")

    monkeypatch.setattr("custom_components.pawbby.config_flow._test_connection", fake)


async def _create_entry(hass: HomeAssistant, monkeypatch):
    """Run the config flow (live check + Tuya client mocked) and return the entry."""
    _patch_connect(monkeypatch)
    monkeypatch.setattr(
        "custom_components.pawbby.coordinator.PawbbyTuyaClient", FakeClient
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_DEVICE_ID: "dev123", CONF_LOCAL_KEY: "key", CONF_IP: "192.168.1.50"},
    )
    assert result["type"] == "form" and result["step_id"] == "cats"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cat_1_name": "Milo", "cat_1_weight": 4.7}
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.data[CONF_CATS] == [{"id": "cat_1", "name": "Milo", "weight_kg": 4.7}]
    await hass.async_block_till_done()
    if entry.state != ConfigEntryState.LOADED:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED
    return entry


async def test_config_flow_cannot_connect(hass: HomeAssistant, monkeypatch):
    _patch_connect(monkeypatch, fail=True)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_DEVICE_ID: "dev123", CONF_LOCAL_KEY: "bad"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_setup_entry_push_events_and_services(hass: HomeAssistant, monkeypatch):
    entry = await _create_entry(hass, monkeypatch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    client = FakeClient.instances[-1]
    assert client.started
    assert client.kwargs["timezone_name"] == hass.config.time_zone

    # push messages -> snapshot update + HA bus events
    fired = []
    hass.bus.async_listen(EVENT_PAWBBY, lambda e: fired.append(e.data))
    await coordinator._on_message({"dps": {"116": "work_idle", "112": 6000}})
    await coordinator._on_message({"dps": {"116": "collect_full"}})
    await hass.async_block_till_done()
    assert coordinator.data["status"] == "Bin Full"
    assert coordinator.data["bin_full"] is True
    assert any(e["type"] == "bin-full" and e["device_id"] == "dev123" for e in fired)

    # visit with PawID -> toileted event with pet
    await coordinator._on_message({"dps": {"116": "cat_enter", "113": 4700}})
    await coordinator._on_message({"dps": {"116": "cat_leave", "107": "AQAABRKJACIA"}})
    await hass.async_block_till_done()
    toileted = [e for e in fired if e["type"] == "toileted"]
    assert toileted and toileted[0]["pet_id"] == "cat_1"
    assert toileted[0]["weight_kg"] == pytest.approx(4.745)
    assert coordinator.data["visits_today"] == 1

    # service: action clean (box idle, settle window elapsed -> allowed)
    await coordinator._on_message({"dps": {"116": "work_idle"}})
    coordinator.state_machine.state.last_visit_ended_at = 0  # pretend the cat left long ago
    await hass.services.async_call(
        DOMAIN, "action", {CONF_DEVICE_ID: "dev123", "action": "clean"}, blocking=True
    )
    assert client.sent_actions == ["clean"]

    # service: set_setting
    await hass.services.async_call(
        DOMAIN,
        "set_setting",
        {CONF_DEVICE_ID: "dev123", "setting": "auto_clean", "value": True},
        blocking=True,
    )
    assert client.sent_settings[-1].payload == "AQAAAQE="
    assert any(e["type"] == "settings-changed" for e in fired)

    # service: tare
    await hass.services.async_call(
        DOMAIN, "tare", {CONF_DEVICE_ID: "dev123"}, blocking=True
    )
    assert client.tare_calls == 1

    # busy guard: box reports work_mclean -> flatten must be refused
    await coordinator._on_message({"dps": {"116": "work_mclean"}})
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "action", {CONF_DEVICE_ID: "dev123", "action": "flatten"}, blocking=True
        )
    assert client.sent_actions == ["clean"]  # unchanged

    # unknown device id -> validation error
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "action", {CONF_DEVICE_ID: "nope", "action": "clean"}, blocking=True
        )

    # unload stops the client
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not client.started
    assert entry.state == ConfigEntryState.NOT_LOADED
