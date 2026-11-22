"""Constants for the Pawbby Smart Litter Box integration.

Pure Python — no Home Assistant imports — so the protocol layer can be
unit-tested without a running HA instance.

Protocol reverse-engineered in the Pawbby-Reborn project (AGPL-3.0);
see VALUES.md and DP105_SETTINGS.md there for the full hardware-confirmed
write-up. This integration is licensed AGPL-3.0 as well.
"""

from __future__ import annotations

DOMAIN = "pawbby"

# --- Tuya datapoint ids (VALUES.md "Complete DP Map") ------------------------
DP_DEVICE_STATUS = 103    # settings + live-flags snapshot, push-only (not in status())
DP_DEVICE_GATE = 105      # settings write DP ("deviceGate")
DP_CLEAN_CONTROL = 106    # motor commands ("clean_control")
DP_TOILET_DATA = 107      # visit summary: weight + duration, broadcast on real visits
DP_TARE = 109             # weight calibration / zero scale
DP_WEIGHT_FILTERED = 112  # filtered scale weight in grams (idle base weight)
DP_CAT_WEIGHT_LIVE = 113  # live cat weight during a visit (grams); also numeric ACK payloads
DP_FLAG_1 = 114           # event flag enum (motor_ok, deodorant_reset, faults...)
DP_FLAG_2 = 115           # settings ACK enum
DP_STATE = 116            # device state machine enum (READ-ONLY)

TUYA_PROTOCOL_VERSION = "3.4"


class DeviceGate:
    """DP 105 `deviceGate` command words."""

    AUTO_CLEAR = 0x00        # auto-clean on/off
    SOFT_CLUMPS = 0x01       # "Soft Clumps Mode" (shake 2-4x before cleaning)
    SLEEP = 0x02             # quiet period / Do-Not-Disturb on/off
    AUTO_OFF_SCREEN = 0x03   # auto screen-off (5 min)
    CHILD_LOCK = 0x04        # screen lock
    LITTER_TYPE = 0x05       # litter type index
    SET_AUTO_CLEAR_TIME = 0x06  # auto-clean delay, minutes
    SET_DISTURB_TIME = 0x07     # quiet period start+stop window
    RESET_DEODORANT = 0x08      # reset deodorant pod counter (always 60 days)
    SYNC_TIME_ZONE = 0x09       # UTC offset in whole hours
    SET_WEIGHT_UNIT = 0x0A      # 0 = kg, 1 = lb


# --- DP 106 motor commands + DP 109 tare (base64 payloads, hardware-confirmed)
DP106_COMMANDS = {
    "clean": "AQAAAA==",         # 01 00 00 00        -> work_mclean
    "flatten": "AQEAAQA=",       # 01 01 00 01 00     -> work_smooth
    "empty": "AQIAAQA=",         # 01 02 00 01 00     -> work_empty (dumps ALL litter!)
    "cancel_clean": "AQMAAA==",  # 01 03 00 01 00     -> cancel clean cycle
}
DP109_TARE_PAYLOAD = "AQEAAA=="  # 01 01 00 00 -> zero the scale

LITTER_TYPES = [
    {"id": 0, "name": "Pawbby Natural Cat Litter", "hint": "Plant-based (recommended by the vendor)"},
    {"id": 1, "name": "Tofu cat litter", "hint": ""},
    {"id": 2, "name": "Bentonite cat litter", "hint": "Clay"},
    {"id": 3, "name": "Mixed cat litter", "hint": "Tofu + bentonite blend"},
]

# --- DP 116 state machine states (read-only, reported by the box) ------------
DP116_STATES = frozenset({
    "work_idle", "cat_near", "cat_enter", "cat_leave", "cat_near_leave",
    "work_smooth", "work_aclean", "work_mclean", "work_empty", "work_dumping",
    "work_resetting", "lid_open", "lid_close", "roller_uninstall_ok",
    "collect_install", "collect_full", "collect_normal",
    "cat_litter_little", "cat_litter_enough",
})

# States in which motor commands (clean/flatten/empty/tare) must be refused.
BUSY_STATES = frozenset({
    "unknown", "cat_enter", "cat_near", "cat_near_leave", "cat_leave",
    "lid_open", "collect_install", "roller_uninstall_ok", "work_smooth",
    "work_aclean", "work_mclean", "work_empty", "work_dumping", "work_resetting",
})

# DP 114 is an event flag, not a pure motor-health field: only fault-looking
# values count as motor errors (a `deodorant_reset` echo is benign).
BENIGN_DP114 = frozenset({"motor_ok", "deodorant_reset"})
MOTOR_FAULT_PATTERN = ("motor", "err", "fault", "fail", "stall", "stuck", "block", "over", "timeout")

# Event types fired on the HA event bus (replaces Pawbby-Reborn's webhooks).
# "-button" variants are triggered by the box's physical button; the plain
# variants are triggered via this integration (HA service/entity call).
EVENT_TYPES = [
    "toileted", "quick-visit",
    "auto-clean", "manual-clean", "manual-clean-button", "clean-completed",
    "flatten", "auto-flatten", "flatten-button",
    "empty", "empty-button",
    "lid-removed", "lid-replaced",
    "bin-removed", "bin-replaced", "bin-full", "bin-normal",
    "litter-low", "litter-sufficient",
    "drum-removed", "drum-installed",
    "litter-added", "litter-removed",
    "settings-changed",
]

# PawID: a visit is attributed to the cat whose profile weight is closest,
# within this threshold (kg). Matches Pawbby-Reborn (300 g).
PAWID_MATCH_THRESHOLD_KG = 0.3

# Weight plausibility window for live/firmware weight samples (grams).
WEIGHT_MIN_G = 500
WEIGHT_MAX_G = 25000

# A cat re-entering within this many seconds merges into the previous visit.
SPLIT_VISIT_WINDOW_S = 15

# --- config entry keys / options ----------------------------------------------
CONF_DEVICE_ID = "device_id"      # Tuya device id
CONF_LOCAL_KEY = "local_key"      # Tuya local key
CONF_IP = "ip"                    # optional static IP (else broadcast discovery)
CONF_CATS = "cats"                # [{id, name, weight_kg}]
CONF_SCAN_INTERVAL = "scan_interval"  # fallback poll interval, seconds
CONF_WEIGHT_UNIT = "weight_unit"  # "kg" | "lb" (mirrored onto the box, DP 105 gate 0A)

DEFAULT_SCAN_INTERVAL = 30
MAX_CATS = 4

# HA event bus event fired for every litter-box event (type + payload).
EVENT_PAWBBY = f"{DOMAIN}_event"
