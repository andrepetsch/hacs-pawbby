"""Litter-box state machine — port of ``handleData`` in Pawbby-Reborn's
``web/server/plugins/tuya-listener.ts``.

Pure Python, no HA imports: the coordinator feeds raw Tuya ``dps`` dicts in
and gets back (events, setting-commands-to-send). All timings use an injectable
clock so the whole machine is deterministically testable.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .const import (
    BENIGN_DP114,
    MOTOR_FAULT_PATTERN,
    SPLIT_VISIT_WINDOW_S,
    WEIGHT_MAX_G,
    WEIGHT_MIN_G,
)
from .pawid import CatProfile, match_cat
from .protocol import (
    DeviceSettings,
    SettingCommand,
    build_setting_command,
    decode_device_status,
    decode_toilet_data,
)

# DP 116 states during which a cat visit is being tracked.
VISIT_STATUSES = frozenset({"cat_enter", "cat_near", "cat_near_leave", "cat_leave"})

EVENT_DEDUP_S = 60.0            # clean / flatten / empty repeats
CLEAN_COMPLETED_DEDUP_S = 30.0  # DP 102 + return-to-idle both signal completion
APP_ACTION_WINDOW_S = 10.0      # command sent by us within the last 10 s
LITTER_DELTA_G = 50             # base-weight change that counts as litter added/removed
WEIGHT_UNIT_SYNC_COOLDOWN_S = 600.0
AUTO_FLATTEN_AFTER_CAT_S = 300.0  # work_smooth within 5 min of a cat leaving = auto-flatten


@dataclass
class LitterEvent:
    type: str
    timestamp: float
    pet_id: str | None = None
    weight_kg: float | None = None
    duration_s: int | None = None
    description: str = ""


@dataclass
class BoxState:
    status_raw: str = "unknown"
    motor_error: bool = False
    base_weight_g: int = 0
    peak_weight_g: int = 0
    cat_entered_at: float | None = None
    lid_opened_during_visit: bool = False
    last_visit_ended_at: float | None = None
    last_visit_end_weight_g: int = 0
    is_bin_full: bool = False
    is_litter_low: bool = False
    is_drum_removed: bool = False
    pending_litter_check: bool = False
    last_clean: float = 0.0
    last_flatten: float = 0.0
    last_empty: float = 0.0
    last_cat_leave: float = 0.0
    last_clean_completed: float = 0.0
    last_weight_unit_sync: float = 0.0
    settings: DeviceSettings | None = None
    settings_updated_at: float | None = None
    last_settings_raw: str | None = None
    last_heartbeat: float | None = None


class StateMachine:
    """Tracks one litter box from raw Tuya dps messages.

    ``handle_dps`` returns ``(events, commands)``: newly generated litter
    events and settings commands that should be sent back to the box
    (weight-unit sync). Events are also kept in a bounded in-memory log so
    sensors like "visits today" / "last visit" can be derived without a DB.
    """

    def __init__(
        self,
        cats: list[CatProfile] | None = None,
        *,
        desired_weight_unit: str = "kg",
        now: Callable[[], float] = time.time,
        max_events: int = 1000,
    ) -> None:
        self.cats = list(cats or [])
        self._desired_weight_unit = desired_weight_unit
        self._now = now
        self.state = BoxState()
        self.events: deque[LitterEvent] = deque(maxlen=max_events)
        self._last_app_action = 0.0

    def mark_app_action(self) -> None:
        """Call when this integration itself sent a motor command."""
        self._last_app_action = self._now()

    # ------------------------------------------------------------------ input

    def handle_dps(
        self, dps: dict[str, Any]
    ) -> tuple[list[LitterEvent], list[SettingCommand]]:
        events: list[LitterEvent] = []
        commands: list[SettingCommand] = []
        st = self.state
        now = self._now()
        st.last_heartbeat = now

        def emit(type: str, **kw: Any) -> None:
            ev = LitterEvent(type=type, timestamp=now, **kw)
            events.append(ev)
            self.events.append(ev)

        # 0. DP 102 — clean cycle result reporter (broadcast at END of a clean)
        if dps.get("102"):
            if not st.last_clean_completed or now - st.last_clean_completed > CLEAN_COMPLETED_DEDUP_S:
                st.last_clean_completed = now
                emit("clean-completed")

        # 1. DP 107 — authoritative visit summary (weight + duration)
        if "107" in dps:
            duration_s = 60  # fallback: 1 min if we missed the entry event
            weight_kg = 0.0
            if st.cat_entered_at is not None and st.peak_weight_g > 0:
                duration_s = round(now - st.cat_entered_at)
                weight_kg = st.peak_weight_g / 1000
            if isinstance(dps["107"], str):
                td = decode_toilet_data(dps["107"])
                if td is not None:
                    if WEIGHT_MIN_G < td.weight_g < WEIGHT_MAX_G:
                        weight_kg = td.weight_g / 1000
                    if td.duration_s > 0:
                        duration_s = td.duration_s
            cat = match_cat(weight_kg, self.cats) if weight_kg > 0 else None
            emit(
                "toileted",
                pet_id=cat.id if cat else None,
                weight_kg=weight_kg if weight_kg > 0 else None,
                duration_s=duration_s,
                description=f"{cat.name if cat else 'Unknown cat'} used the box",
            )
            st.last_visit_ended_at = now
            st.last_visit_end_weight_g = round(weight_kg * 1000) if weight_kg > 0 else st.peak_weight_g
            st.cat_entered_at = None
            st.peak_weight_g = 0
            st.lid_opened_during_visit = False

        # 1b. DP 103 — settings + live-flags snapshot
        if isinstance(dps.get("103"), str):
            decoded = decode_device_status(dps["103"])
            if decoded is not None:
                st.last_settings_raw = dps["103"]
                st.settings = decoded
                st.settings_updated_at = now
                # keep the box's weight unit aligned with the HA config
                if now - st.last_weight_unit_sync > WEIGHT_UNIT_SYNC_COOLDOWN_S:
                    if decoded.weight_unit != self._desired_weight_unit:
                        st.last_weight_unit_sync = now
                        commands.append(
                            build_setting_command("weight_unit", self._desired_weight_unit)
                        )

        # 2. DP 116 — device state machine (read-only)
        if "116" in dps:
            new = str(dps["116"])
            old = st.status_raw
            is_app = now - self._last_app_action < APP_ACTION_WINDOW_S

            if new == "work_aclean" and old != "work_aclean":
                if now - st.last_clean > EVENT_DEDUP_S:
                    emit("auto-clean")
                    st.last_clean = now
            if new == "work_mclean" and old != "work_mclean":
                if now - st.last_clean > EVENT_DEDUP_S:
                    emit("manual-clean" if is_app else "manual-clean-button")
                    st.last_clean = now
            if old in ("work_aclean", "work_mclean") and new in ("work_idle", "collect_normal"):
                if not st.last_clean_completed or now - st.last_clean_completed > CLEAN_COMPLETED_DEDUP_S:
                    st.last_clean_completed = now
                    emit("clean-completed")
            if new in ("cat_leave", "cat_near_leave"):
                st.last_cat_leave = now
            if new == "work_smooth" and old != "work_smooth":
                if now - st.last_flatten > EVENT_DEDUP_S:
                    if is_app:
                        emit("flatten")
                    elif now - st.last_cat_leave < AUTO_FLATTEN_AFTER_CAT_S:
                        emit("auto-flatten")
                    else:
                        emit("flatten-button")
                    st.last_flatten = now
            if new == "work_empty" and old != "work_empty":
                if now - st.last_empty > EVENT_DEDUP_S:
                    emit("empty" if is_app else "empty-button")
                    st.last_empty = now
            if new == "lid_open" and old != "lid_open":
                if st.cat_entered_at is not None:
                    st.lid_opened_during_visit = True
                emit("lid-removed")
            if old == "lid_open" and new != "lid_open":
                emit("lid-replaced")
                st.pending_litter_check = True
            if new == "collect_install" and old != "collect_install":
                emit("bin-removed")
            if old == "collect_install" and new != "collect_install":
                emit("bin-replaced")
            if new == "collect_full" and not st.is_bin_full:
                st.is_bin_full = True
                emit("bin-full")
            if new == "collect_normal" and st.is_bin_full:
                st.is_bin_full = False
                emit("bin-normal")
            if new == "cat_litter_little" and not st.is_litter_low:
                st.is_litter_low = True
                emit("litter-low")
            if (new == "cat_litter_enough" or new.startswith("cat_litter_eno")) and st.is_litter_low:
                st.is_litter_low = False
                emit("litter-sufficient")
            if new == "roller_uninstall_ok" and not st.is_drum_removed:
                st.is_drum_removed = True
                emit("drum-removed")
            if st.is_drum_removed and new != "roller_uninstall_ok":
                st.is_drum_removed = False
                emit("drum-installed")
            # quick visit: back to idle with an unconfirmed visit window
            if new == "work_idle" and st.cat_entered_at is not None:
                if not st.lid_opened_during_visit and st.peak_weight_g > 0:
                    # no pet attribution: half a heavy cat looks like a small cat
                    emit(
                        "quick-visit",
                        pet_id=None,
                        weight_kg=st.peak_weight_g / 1000,
                        duration_s=round(now - st.cat_entered_at),
                    )
                st.last_visit_ended_at = now
                st.last_visit_end_weight_g = st.peak_weight_g
                st.cat_entered_at = None
                st.peak_weight_g = 0
                st.lid_opened_during_visit = False
            st.status_raw = new

        # 3. DP 112 — filtered scale weight while idle (base weight + litter delta)
        if st.status_raw == "work_idle" and isinstance(dps.get("112"), (int, float)):
            if st.pending_litter_check and st.base_weight_g > 0:
                diff = dps["112"] - st.base_weight_g
                if abs(diff) >= LITTER_DELTA_G:
                    emit("litter-added" if diff > 0 else "litter-removed", weight_kg=abs(diff) / 1000)
                st.pending_litter_check = False
            st.base_weight_g = int(dps["112"])

        # 4. cat entry starts the visit window (with split-visit merge)
        if st.status_raw == "cat_enter" and st.cat_entered_at is None:
            if (
                st.last_visit_ended_at is not None
                and now - st.last_visit_ended_at < SPLIT_VISIT_WINDOW_S
                and st.last_visit_end_weight_g > 0
            ):
                st.peak_weight_g = st.last_visit_end_weight_g  # merge split visit
            else:
                st.peak_weight_g = 0
            st.cat_entered_at = now
            st.lid_opened_during_visit = False

        # 5. during a visit: track peak weight from DP 113 (live cat weight)
        #    and DP 112 (scale delta)
        if st.cat_entered_at is not None and st.status_raw in VISIT_STATUSES:
            live = dps.get("113")
            if (
                isinstance(live, (int, float))
                and WEIGHT_MIN_G < live < WEIGHT_MAX_G
                and live > st.peak_weight_g
            ):
                st.peak_weight_g = int(live)
            if isinstance(dps.get("112"), (int, float)) and st.base_weight_g > 0:
                cat_w = dps["112"] - st.base_weight_g
                if st.peak_weight_g < cat_w < WEIGHT_MAX_G:
                    st.peak_weight_g = int(cat_w)

        # 6. DP 114 — event flag; only fault-looking values are motor errors
        if "114" in dps:
            v = str(dps["114"]).lower()
            st.motor_error = v not in BENIGN_DP114 and any(p in v for p in MOTOR_FAULT_PATTERN)

        return events, commands

    # ------------------------------------------------------------------ output

    @property
    def last_visit(self) -> LitterEvent | None:
        for ev in reversed(self.events):
            if ev.type == "toileted":
                return ev
        return None

    def visits_today(self) -> int:
        """Count 'toileted' events since local midnight (injectable clock)."""
        midnight = self._now() - (self._now() % 86400)
        return sum(1 for ev in self.events if ev.type == "toileted" and ev.timestamp >= midnight)

    def snapshot(self) -> dict[str, Any]:
        """Entity-friendly view of the box (mirrors deviceState.ts)."""
        st = self.state
        status = "Ready"
        if st.status_raw == "unknown":
            status = "Unknown"
        elif st.status_raw == "lid_open":
            status = "Lid Open"
        elif st.status_raw == "collect_install":
            status = "Bin Removed"
        elif st.status_raw == "roller_uninstall_ok":
            status = "Drum Removed"
        elif st.status_raw == "collect_full":
            status = "Bin Full"
        elif st.status_raw not in ("work_idle", "collect_normal", "lid_close") and not st.status_raw.startswith(
            "cat_litter_eno"
        ):
            status = "Busy"
        if st.is_drum_removed and status == "Ready":
            status = "Drum Removed"
        if st.is_bin_full and status == "Ready":
            status = "Bin Full"
        if st.motor_error and status == "Ready":
            status = "Motor Error"
        return {
            "status": status,
            "status_raw": st.status_raw,
            "cleaning": st.status_raw in ("work_aclean", "work_mclean"),
            "waste_bin": "Full" if st.is_bin_full else "Normal",
            "bin_full": st.is_bin_full,
            "litter_level": "Low" if st.is_litter_low else "Sufficient",
            "litter_low": st.is_litter_low,
            "lid_open": st.status_raw == "lid_open",
            "bin_removed": st.status_raw == "collect_install",
            "drum_removed": st.is_drum_removed,
            "motor_error": st.motor_error,
            "base_weight_g": st.base_weight_g,
            "peak_weight_g": st.peak_weight_g,
            "cat_inside": st.cat_entered_at is not None,
            "last_heartbeat": st.last_heartbeat,
            "settings": st.settings,
            "settings_updated_at": st.settings_updated_at,
            "last_visit": self.last_visit,
            "visits_today": self.visits_today(),
        }
