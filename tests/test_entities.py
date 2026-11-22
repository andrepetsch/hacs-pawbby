"""Entity tests: platform setup, state values, and entity -> command round-trips.

Entity ids are resolved via the entity registry + unique_id so display-name
slugs don't matter.
"""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from test_ha import FakeClient, _create_entry  # reuse HA test harness pieces

from custom_components.pawbby.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

DP103_FACTORY = "AQAAFQAAAAAWAAAACB4BAAAAAAABAgA7AA=="  # AC on, delay 1, litter 0, 59d
DP107_4745G_34S = "AQAABRKJACIA"


def eid(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"no {platform} entity registered for unique_id {unique_id}"
    return entity_id


def state(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    return hass.states.get(eid(hass, platform, unique_id)).state


async def _setup(hass: HomeAssistant, monkeypatch):
    entry = await _create_entry(hass, monkeypatch)
    return entry, hass.data[DOMAIN][entry.entry_id], FakeClient.instances[-1]


async def test_entities_created(hass: HomeAssistant, monkeypatch):
    entry, coordinator, _ = await _setup(hass, monkeypatch)
    await hass.async_block_till_done()
    for platform, unique_id in (
        ("sensor", "dev123_sensor_status"),
        ("sensor", "dev123_sensor_waste_bin"),
        ("sensor", "dev123_sensor_litter_level"),
        ("sensor", "dev123_sensor_visits_today"),
        ("sensor", "dev123_sensor_last_visit_weight"),
        ("sensor", "dev123_sensor_last_visit_pet"),
        ("sensor", "dev123_sensor_last_visit_at"),
        ("sensor", "dev123_sensor_last_cleaned"),
        ("sensor", "dev123_sensor_deodorizer_days_left"),
        ("binary_sensor", "dev123_binary_online"),
        ("binary_sensor", "dev123_binary_cleaning"),
        ("binary_sensor", "dev123_binary_lid_open"),
        ("binary_sensor", "dev123_binary_bin_full"),
        ("binary_sensor", "dev123_binary_bin_removed"),
        ("binary_sensor", "dev123_binary_drum_removed"),
        ("binary_sensor", "dev123_binary_litter_low"),
        ("binary_sensor", "dev123_binary_motor_error"),
        ("switch", "dev123_switch_auto_clean"),
        ("switch", "dev123_switch_sleep_mode"),
        ("switch", "dev123_switch_soft_clumps"),
        ("switch", "dev123_switch_auto_off_screen"),
        ("switch", "dev123_switch_child_lock"),
        ("number", "dev123_number_auto_clean_delay"),
        ("select", "dev123_select_litter_type"),
        ("button", "dev123_button_clean"),
        ("button", "dev123_button_flatten"),
        ("button", "dev123_button_empty"),
        ("button", "dev123_button_tare"),
        ("button", "dev123_button_cancel_clean"),
        ("event", "dev123_event"),
        # per-cat sensors (Milo)
        ("sensor", "pet_cat_1_sensor_weight"),
        ("sensor", "pet_cat_1_sensor_last_used"),
        ("sensor", "pet_cat_1_sensor_last_duration"),
        ("sensor", "pet_cat_1_sensor_visits_today"),
    ):
        eid(hass, platform, unique_id)
    assert entry.state.name == "LOADED"


async def test_settings_entities_reflect_dp103(hass: HomeAssistant, monkeypatch):
    _, coordinator, _ = await _setup(hass, monkeypatch)
    await coordinator._on_message({"dps": {"103": DP103_FACTORY, "116": "work_idle"}})
    await hass.async_block_till_done()
    assert state(hass, "switch", "dev123_switch_auto_clean") == "on"
    assert state(hass, "switch", "dev123_switch_sleep_mode") == "off"
    assert float(state(hass, "number", "dev123_number_auto_clean_delay")) == 1
    assert state(hass, "select", "dev123_select_litter_type") == "Pawbby Natural Cat Litter"
    assert state(hass, "sensor", "dev123_sensor_deodorizer_days_left") == "59"


async def test_switch_number_select_send_dp105(hass: HomeAssistant, monkeypatch):
    _, coordinator, client = await _setup(hass, monkeypatch)
    await coordinator._on_message({"dps": {"103": DP103_FACTORY, "116": "work_idle"}})
    coordinator.state_machine.state.last_visit_ended_at = 0
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_off",
        {"entity_id": eid(hass, "switch", "dev123_switch_auto_clean")}, blocking=True,
    )
    assert client.sent_settings[-1].payload == "AQAAAQA="  # auto_clean off

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "dev123_number_auto_clean_delay"), "value": 5},
        blocking=True,
    )
    assert client.sent_settings[-1].payload == "AQYAAQU="  # 5 min

    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": eid(hass, "select", "dev123_select_litter_type"),
         "option": "Bentonite cat litter"},
        blocking=True,
    )
    assert client.sent_settings[-1].payload == "AQUAAQI="  # litter type 2


async def test_buttons_send_actions(hass: HomeAssistant, monkeypatch):
    _, coordinator, client = await _setup(hass, monkeypatch)
    await coordinator._on_message({"dps": {"116": "work_idle"}})
    coordinator.state_machine.state.last_visit_ended_at = 0
    await hass.async_block_till_done()

    await hass.services.async_call(
        "button", "press", {"entity_id": eid(hass, "button", "dev123_button_clean")}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": eid(hass, "button", "dev123_button_tare")}, blocking=True
    )
    assert client.sent_actions == ["clean"]
    assert client.tare_calls == 1


async def test_binary_sensor_and_event_entity_follow_push(hass: HomeAssistant, monkeypatch):
    _, coordinator, _ = await _setup(hass, monkeypatch)
    await coordinator._on_message({"dps": {"116": "work_idle"}})
    await coordinator._on_message({"dps": {"116": "collect_full"}})
    await hass.async_block_till_done()
    assert state(hass, "binary_sensor", "dev123_binary_bin_full") == "on"
    assert state(hass, "sensor", "dev123_sensor_waste_bin") == "Full"
    # event entity: last event type is exposed in the event_type attribute
    ev_state = hass.states.get(eid(hass, "event", "dev123_event"))
    assert ev_state.attributes["event_type"] == "bin-full"


async def test_cat_sensors_after_visit(hass: HomeAssistant, monkeypatch):
    _, coordinator, _ = await _setup(hass, monkeypatch)
    await coordinator._on_message({"dps": {"116": "work_idle", "112": 6000}})
    await coordinator._on_message({"dps": {"116": "cat_enter", "113": 4700}})
    await coordinator._on_message({"dps": {"116": "cat_leave", "107": DP107_4745G_34S}})
    await hass.async_block_till_done()
    assert float(state(hass, "sensor", "pet_cat_1_sensor_weight")) == pytest.approx(4.745)
    assert int(state(hass, "sensor", "pet_cat_1_sensor_visits_today")) == 1
    assert float(state(hass, "sensor", "pet_cat_1_sensor_last_duration")) == 34
    assert state(hass, "sensor", "dev123_sensor_last_visit_pet") == "Milo"
    assert int(state(hass, "sensor", "dev123_sensor_visits_today")) == 1
