"""Protocol tests against the hardware-confirmed vectors from Pawbby-Reborn.

Sources:
- DP105_SETTINGS.md (DP 103 samples, DP 105 payload tables, ACK behaviour)
- VALUES.md (DP 107 validation dataset from @managementboy, DP 106 commands)
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from custom_components.pawbby import protocol as p
from custom_components.pawbby.const import DeviceGate

# --- DP 105 gate frames (DP105_SETTINGS.md section 2) ------------------------

AUTO_CLEAN_DELAY_VECTORS = {  # minutes -> base64
    1: "AQYAAQE=",
    5: "AQYAAQU=",
    10: "AQYAAQo=",
    15: "AQYAAQ8=",
    30: "AQYAAR4=",
    60: "AQYAATw=",
}

TOGGLE_VECTORS = [  # (gate, on, base64)
    (DeviceGate.AUTO_CLEAR, True, "AQAAAQE="),
    (DeviceGate.AUTO_CLEAR, False, "AQAAAQA="),
    (DeviceGate.SOFT_CLUMPS, True, "AQEAAQE="),
    (DeviceGate.SOFT_CLUMPS, False, "AQEAAQA="),
    (DeviceGate.SLEEP, True, "AQIAAQE="),
    (DeviceGate.SLEEP, False, "AQIAAQA="),
    (DeviceGate.AUTO_OFF_SCREEN, True, "AQMAAQE="),
    (DeviceGate.AUTO_OFF_SCREEN, False, "AQMAAQA="),
    (DeviceGate.CHILD_LOCK, True, "AQQAAQE="),
    (DeviceGate.CHILD_LOCK, False, "AQQAAQA="),
]


class TestEncodeGateFrame:
    @pytest.mark.parametrize("minutes,expected", AUTO_CLEAN_DELAY_VECTORS.items())
    def test_auto_clean_delay(self, minutes, expected):
        assert p.encode_gate_frame(DeviceGate.SET_AUTO_CLEAR_TIME, [minutes]) == expected
        assert p.encode_auto_clean_delay(minutes) == expected

    @pytest.mark.parametrize("gate,on,expected", TOGGLE_VECTORS)
    def test_toggles(self, gate, on, expected):
        assert p.encode_toggle(gate, on) == expected

    def test_sleep_window_22_00_08_30(self):
        # 01 07 00 04 16 00 08 1e  (factory default window)
        assert p.encode_sleep_window("22:00", "08:30") == "AQcABBYACB4="

    def test_sleep_window_23_30_07_00(self):
        # 01 07 00 04 17 1e 07 00
        assert p.encode_sleep_window("23:30", "07:00") == "AQcABBceBwA="

    def test_reset_deodorant_no_payload(self):
        assert p.encode_reset_deodorant() == "AQgAAA=="  # 01 08 00 00

    def test_time_zone_utc_plus_2(self):
        assert p.encode_gate_frame(DeviceGate.SYNC_TIME_ZONE, [2]) == "AQkAAQI="
        assert p.encode_time_zone(2) == "AQkAAQI="

    def test_time_zone_utc_minus_7_twos_complement(self):
        # 01 09 00 01 F9 — accepted by firmware, signed interpretation unverified
        assert p.encode_time_zone(-7) == "AQkAAfk="

    def test_weight_unit(self):
        assert p.encode_weight_unit("kg") == "AQoAAQA="
        assert p.encode_weight_unit("lb") == "AQoAAQE="

    def test_litter_type_bentonite(self):
        assert p.encode_litter_type(2) == "AQUAAQI="  # 01 05 00 01 02

    def test_byte_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            p.encode_gate_frame(DeviceGate.AUTO_CLEAR, [0x100])
        with pytest.raises(ValueError):
            p.encode_gate_frame(DeviceGate.AUTO_CLEAR, [-1])


class TestEncodeValidation:
    @pytest.mark.parametrize("minutes", [0, 61, -1, 2.5, "5"])
    def test_bad_delay(self, minutes):
        with pytest.raises(ValueError):
            p.encode_auto_clean_delay(minutes)

    @pytest.mark.parametrize("bad", ["24:00", "12:60", "12-00", "", "ab:cd", "123:00"])
    def test_bad_hhmm(self, bad):
        with pytest.raises(ValueError):
            p.parse_hhmm(bad)

    @pytest.mark.parametrize("good,expected", [("8:00", (8, 0)), ("08:00", (8, 0)), ("23:59", (23, 59))])
    def test_hhmm_single_digit_hour_ok(self, good, expected):
        # The vendor app accepts 1-2 digit hours; so do we.
        assert p.parse_hhmm(good) == expected

    def test_same_start_stop_rejected(self):
        with pytest.raises(ValueError):
            p.encode_sleep_window("08:00", "08:00")

    def test_bad_litter_type(self):
        with pytest.raises(ValueError):
            p.encode_litter_type(9)

    def test_bad_weight_unit(self):
        with pytest.raises(ValueError):
            p.encode_weight_unit("st")

    @pytest.mark.parametrize("offset", [15, -13, 2.5])
    def test_bad_utc_offset(self, offset):
        with pytest.raises(ValueError):
            p.encode_time_zone(offset)


# --- DP 103 decoder (DP105_SETTINGS.md sections 3 + 5) -------------------------


class TestDecodeDeviceStatus:
    def test_factory_sample(self):
        # 01 00 00 15 | 00 00 00 00 16 00 00 00 08 1e 01 00 00 00 00 00 01 02 00 3b 00
        s = p.decode_device_status("AQAAFQAAAAAWAAAACB4BAAAAAAABAgA7AA==")
        assert s is not None
        assert s.bin_full is False
        assert s.bin_removed is False
        assert s.lid_open is False
        assert s.weight_unit == "kg"
        assert s.sleep_start == "22:00"
        assert s.sleep_stop == "08:30"
        assert s.cat_in is False
        assert s.cat_near is False
        assert s.auto_clean is True
        assert s.soft_clumps is False
        assert s.sleep_enabled is False
        assert s.auto_off_screen is False
        assert s.child_lock is False
        assert s.litter_type == 0
        assert s.auto_clean_delay_min == 1
        assert s.litter_level_raw == 2
        assert s.deodorant_days == 59
        assert s.cat_in_long_time is False

    def test_hardware_confirmed_sample(self):
        # Live test 2026-10-07: sleep 23:00-09:00, autoClean=1, soft=0, sleep=1,
        # autoScreen=1, childLock=1, delay=20, litter level 2, deodorant=60
        s = p.decode_device_status("AQAAFQAAAAAXAAAACQABAAEBAQAUAgA8AA==")
        assert s is not None
        assert s.sleep_start == "23:00"
        assert s.sleep_stop == "09:00"
        assert s.auto_clean is True
        assert s.soft_clumps is False
        assert s.sleep_enabled is True
        assert s.auto_off_screen is True
        assert s.child_lock is True
        assert s.auto_clean_delay_min == 20
        assert s.litter_level_raw == 2
        assert s.deodorant_days == 60

    def test_lb_unit(self):
        raw = base64.b64encode(bytes([0x01, 0x00, 0x00, 0x15] + [0] * 3 + [1] + [0] * 17)).decode()
        s = p.decode_device_status(raw)
        assert s is not None
        assert s.weight_unit == "lb"

    @pytest.mark.parametrize("bad", ["", "not base64!!", "AAAA", "AQ==", "AQAAFQ=="])
    def test_malformed_returns_none(self, bad):
        assert p.decode_device_status(bad) is None

    def test_short_payload_returns_none(self):
        # header claims len 21 but only 5 data bytes -> < 17 -> None
        raw = base64.b64encode(bytes([0x01, 0x00, 0x00, 0x15, 1, 2, 3, 4, 5])).decode()
        assert p.decode_device_status(raw) is None


# --- DP 107 decoder (VALUES.md validation dataset, @managementboy) ------------

TOILET_VECTORS = [  # (base64, weight_g, duration_s)
    ("AQAABRKJACIA", 4745, 34),
    ("AQAABRKyADgA", 4786, 56),
    ("AQAABRKaACsA", 4762, 43),
    ("AQAABQ/7AAsA", 4091, 11),
    ("AQAABRASABsA", 4114, 27),
    ("AQAABRATAAsA", 4115, 11),
    ("AQAABRJ2AAwA", 4726, 12),
]


class TestDecodeToiletData:
    @pytest.mark.parametrize("payload,weight,duration", TOILET_VECTORS)
    def test_visit_samples(self, payload, weight, duration):
        t = p.decode_toilet_data(payload)
        assert t is not None
        assert t.weight_g == weight
        assert t.duration_s == duration

    def test_malformed_returns_none(self):
        assert p.decode_toilet_data("AAAA") is None
        # right length but wrong header
        assert p.decode_toilet_data(base64.b64encode(bytes(9)).decode()) is None


# --- build_setting_command -----------------------------------------------------


class TestBuildSettingCommand:
    def test_auto_clean(self):
        c = p.build_setting_command("auto_clean", True)
        assert c.key == "auto_clean"
        assert c.payload == "AQAAAQE="
        c = p.build_setting_command("auto_clean", "off")
        assert c.payload == "AQAAAQA="

    def test_delay(self):
        assert p.build_setting_command("auto_clean_delay", 5).payload == "AQYAAQU="
        assert p.build_setting_command("auto_clean_delay", "20").payload == "AQYAARQ="

    def test_sleep_window(self):
        c = p.build_setting_command("sleep_window", {"start": "22:00", "stop": "08:30"})
        assert c.payload == "AQcABBYACB4="

    def test_sleep_window_missing_fields(self):
        with pytest.raises(ValueError):
            p.build_setting_command("sleep_window", {"start": "22:00"})

    def test_litter_type_by_id_and_name(self):
        assert p.build_setting_command("litter_type", 2).payload == "AQUAAQI="
        assert p.build_setting_command("litter_type", "Tofu cat litter").payload == "AQUAAQE="
        assert p.build_setting_command("litter_type", "bentonite").payload == "AQUAAQI="

    def test_reset_deodorant(self):
        assert p.build_setting_command("reset_deodorant", None).payload == "AQgAAA=="

    def test_sync_timezone(self):
        assert p.build_setting_command("sync_timezone", 2).payload == "AQkAAQI="
        assert p.build_setting_command("sync_timezone", -7).payload == "AQkAAfk="

    def test_weight_unit(self):
        assert p.build_setting_command("weight_unit", "lb").payload == "AQoAAQE="

    def test_unknown_key(self):
        with pytest.raises(ValueError, match="Unknown setting"):
            p.build_setting_command("warp_drive", 1)

    def test_refresh_rejected(self):
        with pytest.raises(ValueError, match="refresh"):
            p.build_setting_command("refresh", None)

    @pytest.mark.parametrize("v", ["1", "true", "ON", "yes", "enable", 1, True])
    def test_to_bool_true(self, v):
        assert p.to_bool(v) is True

    @pytest.mark.parametrize("v", ["0", "false", "OFF", "no", "disable", 0, False])
    def test_to_bool_false(self, v):
        assert p.to_bool(v) is False

    def test_to_bool_invalid(self):
        with pytest.raises(ValueError):
            p.to_bool("maybe")


# --- time zone helper ------------------------------------------------------------


class TestUtcOffsetHoursFor:
    JAN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    JUL = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    def test_utc(self):
        assert p.utc_offset_hours_for("UTC", self.JAN) == 0

    def test_berlin_dst(self):
        assert p.utc_offset_hours_for("Europe/Berlin", self.JAN) == 1
        assert p.utc_offset_hours_for("Europe/Berlin", self.JUL) == 2

    def test_los_angeles_dst(self):
        assert p.utc_offset_hours_for("America/Los_Angeles", self.JAN) == -8
        assert p.utc_offset_hours_for("America/Los_Angeles", self.JUL) == -7
