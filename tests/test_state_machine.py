"""State-machine tests with an injected clock — ports of the scenarios in
Pawbby-Reborn's tuya-listener.ts handleData."""

from __future__ import annotations

import pytest

from custom_components.pawbby.pawid import CatProfile, match_cat
from custom_components.pawbby.state_machine import StateMachine

DP107_4745G_34S = "AQAABRKJACIA"  # 01 00 00 05 12 89 00 22 00 (VALUES.md dataset)
DP103_FACTORY = "AQAAFQAAAAAWAAAACB4BAAAAAAABAgA7AA=="  # sleep 22:00-08:30, AC on, 1 min, 59d


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make_sm(cats=(), **kw):
    clock = kw.pop("clock", Clock())
    sm = StateMachine(list(cats), now=clock, **kw)
    return sm, clock


CATS = [CatProfile("c1", "Milo", 4.7), CatProfile("c2", "Luna", 4.1)]


# --- pawid ---------------------------------------------------------------------


class TestMatchCat:
    def test_closest_within_threshold(self):
        assert match_cat(4.745, CATS).id == "c1"
        assert match_cat(4.11, CATS).id == "c2"

    def test_outside_threshold(self):
        assert match_cat(5.5, CATS) is None
        assert match_cat(0, CATS) is None

    def test_boundary_inclusive(self):
        assert match_cat(4.7 + 0.3, CATS).id == "c1"
        assert match_cat(4.7 + 0.31, CATS) is None


# --- visit flow ------------------------------------------------------------------


def test_full_visit_with_dp107_and_pawid():
    sm, clock = make_sm(CATS)
    sm.handle_dps({"116": "work_idle", "112": 6000})
    sm.handle_dps({"116": "cat_enter"})
    clock.advance(30)
    sm.handle_dps({"116": "cat_enter", "113": 4700})
    clock.advance(4)
    events, _ = sm.handle_dps({"116": "cat_leave", "107": DP107_4745G_34S})
    assert [e.type for e in events] == ["toileted"]
    ev = events[0]
    assert ev.pet_id == "c1"  # 4.745 kg -> Milo (4.7)
    assert ev.weight_kg == pytest.approx(4.745)
    assert ev.duration_s == 34  # firmware byte 7 wins over local estimate
    assert sm.last_visit.pet_id == "c1"
    snap = sm.snapshot()
    assert snap["visits_today"] == 1
    assert snap["cat_inside"] is False


def test_quick_visit_without_pet_attribution():
    sm, clock = make_sm(CATS)
    sm.handle_dps({"116": "work_idle", "112": 6000})
    sm.handle_dps({"116": "cat_enter"})
    clock.advance(5)
    sm.handle_dps({"116": "cat_near", "112": 6000 + 4200})  # 4.2 kg delta, no DP 107
    clock.advance(5)
    events, _ = sm.handle_dps({"116": "work_idle"})
    assert [e.type for e in events] == ["quick-visit"]
    assert events[0].pet_id is None
    assert events[0].weight_kg == pytest.approx(4.2)
    assert events[0].duration_s == 10


def test_lid_open_during_visit_discards_quick_visit():
    sm, clock = make_sm(CATS)
    all_events = []
    for dps in (
        {"116": "work_idle", "112": 6000},
        {"116": "cat_enter"},
        {"116": "lid_open", "112": 6000 + 4000},
        {"116": "work_idle"},
    ):
        events, _ = sm.handle_dps(dps)
        all_events += events
    types = [e.type for e in all_events]
    assert "lid-removed" in types
    assert "lid-replaced" in types
    assert "quick-visit" not in types


def test_split_visit_merges_within_15s():
    sm, clock = make_sm(CATS)
    sm.handle_dps({"116": "work_idle", "112": 6000})
    sm.handle_dps({"116": "cat_enter"})
    sm.handle_dps({"116": "cat_enter", "113": 4700})
    clock.advance(30)
    sm.handle_dps({"116": "cat_near_leave", "107": DP107_4745G_34S})
    clock.advance(10)  # re-enter within 15 s
    sm.handle_dps({"116": "cat_enter"})
    assert sm.state.peak_weight_g == 4745  # carried over from merged visit
    clock.advance(20)
    sm.handle_dps({"116": "cat_near_leave", "107": DP107_4745G_34S})
    assert sm.snapshot()["visits_today"] == 2


def test_reenter_after_15s_does_not_merge():
    sm, clock = make_sm(CATS)
    sm.handle_dps({"116": "work_idle", "112": 6000})
    sm.handle_dps({"116": "cat_enter", "113": 4700})
    clock.advance(30)
    sm.handle_dps({"116": "cat_near_leave", "107": DP107_4745G_34S})
    clock.advance(20)  # > 15 s split window
    sm.handle_dps({"116": "cat_enter"})
    assert sm.state.peak_weight_g == 0


# --- clean cycles -----------------------------------------------------------------


def test_manual_clean_app_vs_button_and_completion():
    sm, clock = make_sm()
    sm.handle_dps({"116": "work_idle"})
    sm.mark_app_action()
    events, _ = sm.handle_dps({"116": "work_mclean"})
    assert [e.type for e in events] == ["manual-clean"]
    clock.advance(70)
    events, _ = sm.handle_dps({"116": "work_idle"})
    assert "clean-completed" in [e.type for e in events]
    # physical button (no app action) -> manual-clean-button; dedup within 60 s
    clock.advance(70)
    events, _ = sm.handle_dps({"116": "work_mclean"})
    assert [e.type for e in events] == ["manual-clean-button"]
    clock.advance(1)  # repeat within dedup window
    events, _ = sm.handle_dps({"116": "work_mclean"})
    assert events == []


def test_auto_clean_and_dp102_completion_dedup():
    sm, clock = make_sm()
    sm.handle_dps({"116": "work_idle"})
    clock.advance(70)
    events, _ = sm.handle_dps({"116": "work_aclean"})
    assert [e.type for e in events] == ["auto-clean"]
    clock.advance(120)
    events, _ = sm.handle_dps({"116": "work_idle", "102": "AQAACwAAAAAAAAAAAAAA"})
    types = [e.type for e in events]
    assert types.count("clean-completed") == 1  # DP 116 return + DP 102 deduped
    assert sm.snapshot()["cleaning"] is False


def test_flatten_auto_after_cat():
    sm, clock = make_sm()
    sm.handle_dps({"116": "work_idle"})
    sm.handle_dps({"116": "cat_enter"})
    clock.advance(30)
    sm.handle_dps({"116": "cat_leave"})
    clock.advance(60)
    events, _ = sm.handle_dps({"116": "work_smooth"})
    assert [e.type for e in events] == ["auto-flatten"]
    clock.advance(70)
    events, _ = sm.handle_dps({"116": "work_smooth"})  # 60s dedup passed, but old==new
    assert events == []


# --- hardware flags ----------------------------------------------------------------


def test_bin_full_normal_removed_replaced():
    sm, _ = make_sm()
    sm.handle_dps({"116": "collect_full"})
    assert sm.state.is_bin_full
    sm.handle_dps({"116": "collect_normal"})
    assert not sm.state.is_bin_full
    sm.handle_dps({"116": "collect_full"})
    events, _ = sm.handle_dps({"116": "collect_install"})
    assert "bin-removed" in [e.type for e in events]
    events, _ = sm.handle_dps({"116": "work_idle"})
    assert "bin-replaced" in [e.type for e in events]
    snap = sm.snapshot()
    assert snap["bin_full"] is True
    assert snap["waste_bin"] == "Full"
    assert snap["bin_removed"] is False


def test_litter_low_sufficient():
    sm, _ = make_sm()
    events, _ = sm.handle_dps({"116": "cat_litter_little"})
    assert [e.type for e in events] == ["litter-low"]
    assert sm.snapshot()["litter_level"] == "Low"
    events, _ = sm.handle_dps({"116": "cat_litter_enough"})
    assert [e.type for e in events] == ["litter-sufficient"]
    assert sm.snapshot()["litter_low"] is False


def test_drum_removed_installed():
    sm, _ = make_sm()
    events, _ = sm.handle_dps({"116": "roller_uninstall_ok"})
    assert [e.type for e in events] == ["drum-removed"]
    assert sm.snapshot()["status"] == "Drum Removed"
    events, _ = sm.handle_dps({"116": "work_idle"})
    assert "drum-installed" in [e.type for e in events]


def test_lid_removed_triggers_litter_delta_check():
    sm, _ = make_sm()
    all_events = []
    for dps in (
        {"116": "work_idle", "112": 6000},
        {"116": "lid_open"},
        {"116": "lid_close"},
        {"116": "work_idle", "112": 6000 + 120},  # +120 g litter
    ):
        events, _ = sm.handle_dps(dps)
        all_events += events
    types = [e.type for e in all_events]
    assert "lid-removed" in types and "lid-replaced" in types and "litter-added" in types
    added = next(e for e in all_events if e.type == "litter-added")
    assert added.weight_kg == pytest.approx(0.12)
    # small change below the 50 g threshold -> no litter event
    all_events = []
    for dps in (
        {"116": "lid_open"},
        {"116": "lid_close"},
        {"116": "work_idle", "112": 6120 + 30},
    ):
        events, _ = sm.handle_dps(dps)
        all_events += events
    assert "litter-added" not in [e.type for e in all_events]
    assert "litter-removed" not in [e.type for e in all_events]


def test_motor_error_only_on_fault_looking_dp114():
    sm, _ = make_sm()
    sm.handle_dps({"116": "work_idle"})
    sm.handle_dps({"114": "motor_ok"})
    assert sm.state.motor_error is False
    sm.handle_dps({"114": "deodorant_reset"})  # benign echo, sticky for ~15 min
    assert sm.state.motor_error is False
    sm.handle_dps({"114": "motor_stall"})
    assert sm.state.motor_error is True
    assert sm.snapshot()["status"] == "Motor Error"  # only when otherwise Ready


# --- settings -----------------------------------------------------------------------


def test_dp103_settings_decoded():
    sm, _ = make_sm()
    sm.handle_dps({"103": DP103_FACTORY})
    s = sm.state.settings
    assert s is not None
    assert s.sleep_start == "22:00" and s.sleep_stop == "08:30"
    assert s.auto_clean is True and s.auto_clean_delay_min == 1
    assert s.deodorant_days == 59
    assert sm.snapshot()["settings"].raw == DP103_FACTORY


def test_weight_unit_mismatch_triggers_sync_once_per_cooldown():
    sm, clock = make_sm(desired_weight_unit="lb")
    # DP 103 factory sample reports kg -> mismatch -> sync command
    _, commands = sm.handle_dps({"103": DP103_FACTORY})
    assert len(commands) == 1
    assert commands[0].key == "weight_unit"
    assert commands[0].payload == "AQoAAQE="  # lb
    # second snapshot within the 10-min cooldown -> no new command
    clock.advance(60)
    _, commands = sm.handle_dps({"103": DP103_FACTORY})
    assert commands == []
    # after the cooldown -> command again
    clock.advance(600)
    _, commands = sm.handle_dps({"103": DP103_FACTORY})
    assert len(commands) == 1


# --- snapshot ------------------------------------------------------------------------


def test_events_not_suppressed_on_freshly_booted_machine():
    """Regression: with a monotonic clock (seconds since boot), uptime 30 s
    must NOT make the 60 s event-dedup swallow the very first events."""
    sm = StateMachine(now=lambda: 30.0)
    sm.handle_dps({"116": "work_idle"})
    events, _ = sm.handle_dps({"116": "work_aclean"})
    assert [e.type for e in events] == ["auto-clean"]
    events, _ = sm.handle_dps({"116": "collect_full"})
    assert [e.type for e in events] == ["bin-full"]
    # weight-unit sync cooldown (600 s) must not block the first sync either
    sm2 = StateMachine(now=lambda: 30.0, desired_weight_unit="lb")
    _, commands = sm2.handle_dps({"103": DP103_FACTORY})  # box reports kg, want lb
    assert len(commands) == 1 and commands[0].payload == "AQoAAQE="


def test_snapshot_status_mapping():
    sm, _ = make_sm()
    assert sm.snapshot()["status"] == "Unknown"  # no DP 116 seen yet
    sm.handle_dps({"116": "work_idle"})
    assert sm.snapshot()["status"] == "Ready"
    sm.handle_dps({"116": "work_mclean"})
    snap = sm.snapshot()
    assert snap["status"] == "Busy" and snap["cleaning"] is True
    sm.handle_dps({"116": "lid_open"})
    assert sm.snapshot()["status"] == "Lid Open"
    sm.handle_dps({"116": "collect_install"})
    assert sm.snapshot()["status"] == "Bin Removed"
