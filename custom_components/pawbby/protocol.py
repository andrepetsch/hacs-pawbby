"""Pawbby litter box protocol codec.

Ported from Pawbby-Reborn ``web/server/utils/deviceSettings.ts`` (AGPL-3.0),
plus the DP 107 visit decoder from ``web/server/plugins/tuya-listener.ts``.
Reverse-engineering notes: ``VALUES.md`` / ``DP105_SETTINGS.md`` in the
Pawbby-Reborn repository (hardware-confirmed 2026-10-07).

The box exposes its user settings on two raw Tuya datapoints:

- DP 103 ``deviceStatus``: read-only snapshot blob pushed by the device
  (every ~10 min and on each state change, and after every DP 105 write).
  Carries all switches, the quiet window and the auto-clean delay.
  Push-only: not returned by DP_QUERY and the firmware ignores DP_REFRESH
  for it — the only way to force a snapshot is a DP 105 write (re-pushing
  the time zone, gate 9, is the harmless choice).
- DP 105 ``deviceGate``: write-only settings DP. Every command is
  ``01 <gate> <len:2 BE> <data>`` encoded as base64 on the wire.

DP 106 carries the motor commands (clean/flatten/empty/cancel), DP 109 the
tare. Everything here is pure (no I/O) so it can be unit-tested.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .const import DeviceGate, LITTER_TYPES

# ---------------------------------------------------------------------------
# DP 105 frame codec
# ---------------------------------------------------------------------------


def encode_gate_frame(gate: int, data: bytes | list[int] = b"") -> str:
    """Build a DP 105 frame ``01 <gate> <len:2 BE> <data>`` and return base64."""
    data = bytes(data)
    for v in data:
        if not 0 <= v <= 0xFF:
            raise ValueError(f"Gate payload byte out of range: {v}")
    frame = bytes([0x01, gate, (len(data) >> 8) & 0xFF, len(data) & 0xFF, *data])
    return base64.b64encode(frame).decode("ascii")


def encode_toggle(gate: int, on: bool) -> str:
    return encode_gate_frame(gate, [1 if on else 0])


def encode_litter_type(litter_id: int) -> str:
    if not isinstance(litter_id, int) or not any(t["id"] == litter_id for t in LITTER_TYPES):
        allowed = ", ".join(f'{t["id"]} ({t["name"]})' for t in LITTER_TYPES)
        raise ValueError(f"Litter type must be one of {allowed}")
    return encode_gate_frame(DeviceGate.LITTER_TYPE, [litter_id])


def encode_auto_clean_delay(minutes: int) -> str:
    if not isinstance(minutes, int) or not 1 <= minutes <= 60:
        raise ValueError("Auto-clean delay must be an integer between 1 and 60 minutes")
    return encode_gate_frame(DeviceGate.SET_AUTO_CLEAR_TIME, [minutes])


def parse_hhmm(value: Any) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m:
        raise ValueError(f'Invalid time "{value}", expected HH:MM')
    h, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        raise ValueError(f'Invalid time "{value}"')
    return h, minute


def encode_sleep_window(start: str, stop: str) -> str:
    """Quiet-period window; times are the device's local time (binary, NOT BCD)."""
    sh, sm = parse_hhmm(start)
    eh, em = parse_hhmm(stop)
    if (sh, sm) == (eh, em):
        raise ValueError("Sleep window start and stop must differ")
    return encode_gate_frame(DeviceGate.SET_DISTURB_TIME, [sh, sm, eh, em])


def encode_reset_deodorant() -> str:
    # No payload: the firmware always resets the counter to 60 days.
    return encode_gate_frame(DeviceGate.RESET_DEODORANT, [])


def encode_weight_unit(unit: str) -> str:
    if unit not in ("kg", "lb"):
        raise ValueError('Weight unit must be "kg" or "lb"')
    return encode_gate_frame(DeviceGate.SET_WEIGHT_UNIT, [1 if unit == "lb" else 0])


def encode_time_zone(offset_hours: int) -> str:
    """UTC offset in whole hours.

    Negative offsets are sent as the two's-complement byte (e.g. UTC-7 -> 0xF9).
    The firmware accepts the frame, but whether it interprets the byte as
    signed is unverified on hardware (see DP105_SETTINGS.md section 5).
    """
    if not isinstance(offset_hours, int) or not -12 <= offset_hours <= 14:
        raise ValueError("UTC offset must be a whole number of hours between -12 and +14")
    byte = offset_hours if offset_hours >= 0 else 0x100 + offset_hours
    return encode_gate_frame(DeviceGate.SYNC_TIME_ZONE, [byte])


def utc_offset_hours_for(time_zone: str, at: datetime | None = None) -> int:
    """Whole-hour UTC offset of an IANA time zone at ``at`` (DST-aware)."""
    at = at or datetime.now(timezone.utc)
    local = at.astimezone(ZoneInfo(time_zone))
    offset = local.utcoffset()
    assert offset is not None
    return round(offset.total_seconds() / 3600)


# ---------------------------------------------------------------------------
# DP 103 snapshot decoder
# ---------------------------------------------------------------------------


def _pad2(n: int) -> str:
    return f"{n:02d}"


@dataclass(frozen=True)
class DeviceSettings:
    """Decoded DP 103 `deviceStatus` snapshot (data-byte layout, see DP105_SETTINGS.md)."""

    bin_full: bool          # byte 0:  1 = waste bin full
    bin_removed: bool       # byte 1:  1 = waste drawer removed
    lid_open: bool          # byte 2:  1 = no cover / lid open
    weight_unit: str        # byte 3:  0 = kg, 1 = lb
    sleep_start: str        # bytes 4-5:  quiet-period start "HH:MM" (device local time)
    cat_in: bool            # byte 6
    cat_near: bool          # byte 7
    sleep_stop: str         # bytes 8-9:  quiet-period stop "HH:MM"
    auto_clean: bool        # byte 10
    soft_clumps: bool       # byte 11
    sleep_enabled: bool     # byte 12: quiet period on/off
    auto_off_screen: bool   # byte 13
    child_lock: bool        # byte 14
    litter_type: int        # byte 15: 0-3 (see LITTER_TYPES)
    auto_clean_delay_min: int  # byte 16: 1-60
    litter_level_raw: int   # byte 17: 0 = empty, 1 = low, 2 = enough
    deodorant_days: int     # byte 19: deodorant pod days left
    cat_in_long_time: bool  # byte 20: 1 = cat in box for a long time
    raw: str                # base64 payload the snapshot was decoded from


def decode_device_status(payload_b64: str) -> DeviceSettings | None:
    """Decode a DP 103 `deviceStatus` payload (base64). Returns None if malformed."""
    try:
        buf = base64.b64decode(payload_b64, validate=True)
    except Exception:
        return None
    # header: ver(1) cmd(1) len(2 BE)
    if len(buf) < 4 or buf[0] != 0x01:
        return None
    length = int.from_bytes(buf[2:4], "big")
    d = buf[4 : 4 + length]
    # The vendor app reads up to byte 21; missing bytes are treated as 0,
    # but we need at least the settings region.
    if len(d) < 17:
        return None

    def b(i: int) -> int:
        return d[i] if i < len(d) else 0

    return DeviceSettings(
        bin_full=b(0) == 1,
        bin_removed=b(1) == 1,
        lid_open=b(2) == 1,
        weight_unit="lb" if b(3) == 1 else "kg",
        sleep_start=f"{_pad2(b(4))}:{_pad2(b(5))}",
        cat_in=b(6) == 1,
        cat_near=b(7) == 1,
        sleep_stop=f"{_pad2(b(8))}:{_pad2(b(9))}",
        auto_clean=b(10) != 0,
        soft_clumps=b(11) != 0,
        sleep_enabled=b(12) != 0,
        auto_off_screen=b(13) != 0,
        child_lock=b(14) != 0,
        litter_type=b(15),
        auto_clean_delay_min=b(16),
        litter_level_raw=b(17),
        deodorant_days=b(19),
        cat_in_long_time=b(20) == 1,
        raw=payload_b64,
    )


# ---------------------------------------------------------------------------
# DP 107 visit decoder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToiletData:
    """DP 107 `toilet_data`: ``01 00 00 05 [WW WW BE grams] 00 [duration s] 00``."""

    weight_g: int
    duration_s: int


def decode_toilet_data(payload_b64: str) -> ToiletData | None:
    """Decode a DP 107 visit summary. Returns None if it does not look like one."""
    try:
        buf = base64.b64decode(payload_b64, validate=True)
    except Exception:
        return None
    if len(buf) < 6 or buf[0] != 0x01 or buf[3] != 0x05:
        return None
    weight_g = int.from_bytes(buf[4:6], "big")
    duration_s = buf[7] if len(buf) >= 8 and buf[7] > 0 else 0
    return ToiletData(weight_g=weight_g, duration_s=duration_s)


# ---------------------------------------------------------------------------
# High-level "setting" API used by services / entities
# ---------------------------------------------------------------------------

# Settings a user may change directly. `weight_unit` and `sync_timezone` are
# deliberately NOT here: they mirror the HA config (weight unit / time zone of
# the HA instance) and are pushed automatically on connect. `refresh` is a
# pseudo-setting handled separately (time-zone push only when no snapshot).
SETTING_KEYS = (
    "auto_clean",
    "soft_clumps",
    "sleep_mode",
    "auto_off_screen",
    "child_lock",
    "auto_clean_delay",
    "sleep_window",
    "litter_type",
    "reset_deodorant",
    "refresh",
)


@dataclass(frozen=True)
class SettingCommand:
    key: str
    payload: str  # base64 payload for DP 105
    description: str  # human-readable, for the event log
    log_event: bool = True  # False -> do not fire a settings-changed event


def to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "on", "yes", "enable", "enabled"):
            return True
        if s in ("0", "false", "off", "no", "disable", "disabled"):
            return False
    raise ValueError(f"Expected a boolean, got {v!r}")


def build_setting_command(key: str, value: Any) -> SettingCommand:
    """Validate a (setting, value) pair and turn it into the DP 105 payload.

    Raises ValueError on invalid input.
    """
    if key == "auto_clean":
        on = to_bool(value)
        return SettingCommand("auto_clean", encode_toggle(DeviceGate.AUTO_CLEAR, on),
                              f"Auto-clean turned {'on' if on else 'off'}")
    if key == "soft_clumps":
        on = to_bool(value)
        return SettingCommand("soft_clumps", encode_toggle(DeviceGate.SOFT_CLUMPS, on),
                              f"Soft clumps mode turned {'on' if on else 'off'}")
    if key == "sleep_mode":
        on = to_bool(value)
        return SettingCommand("sleep_mode", encode_toggle(DeviceGate.SLEEP, on),
                              f"Sleep mode (quiet period) turned {'on' if on else 'off'}")
    if key == "auto_off_screen":
        on = to_bool(value)
        return SettingCommand("auto_off_screen", encode_toggle(DeviceGate.AUTO_OFF_SCREEN, on),
                              f"Auto screen-off turned {'on' if on else 'off'}")
    if key == "child_lock":
        on = to_bool(value)
        return SettingCommand("child_lock", encode_toggle(DeviceGate.CHILD_LOCK, on),
                              f"Screen child lock turned {'on' if on else 'off'}")
    if key == "auto_clean_delay":
        minutes = int(value)
        return SettingCommand("auto_clean_delay", encode_auto_clean_delay(minutes),
                              f"Auto-clean delay set to {minutes} min")
    if key == "sleep_window":
        v = value if isinstance(value, dict) else {}
        if not v.get("start") or not v.get("stop"):
            raise ValueError('sleep_window requires { start: "HH:MM", stop: "HH:MM" }')
        return SettingCommand(
            "sleep_window",
            encode_sleep_window(v["start"], v["stop"]),
            f'Quiet period window set to {v["start"]}-{v["stop"]}',
        )
    if key == "litter_type":
        # Accept the numeric id or a (case-insensitive) name / first word.
        lid: Any = value
        if isinstance(value, str):
            try:
                lid = int(value)
            except ValueError:
                q = value.strip().lower()
                found = next(
                    (t for t in LITTER_TYPES
                     if t["name"].lower() == q or t["name"].lower().split(" ")[0] == q),
                    None,
                )
                lid = found["id"] if found else -1
        name = next((t["name"] for t in LITTER_TYPES if t["id"] == lid), None)
        return SettingCommand("litter_type", encode_litter_type(lid),
                              f"Litter type set to {name if name else lid}")
    if key == "reset_deodorant":
        return SettingCommand("reset_deodorant", encode_reset_deodorant(),
                              "Deodorant pod counter reset on device")
    if key == "weight_unit":
        unit = str(value).lower()
        return SettingCommand("weight_unit", encode_weight_unit(unit),
                              f"Device weight unit set to {unit}")
    if key == "sync_timezone":
        offset = int(value)
        sign = "+" if offset >= 0 else ""
        return SettingCommand("sync_timezone", encode_time_zone(offset),
                              f"Device time zone set to UTC{sign}{offset}")
    if key == "refresh":
        raise ValueError("refresh is dispatched separately, not as a DP 105 payload")
    raise ValueError(f'Unknown setting "{key}". Allowed: {", ".join(SETTING_KEYS)}')
