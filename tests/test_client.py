"""Tests for PawbbyTuyaClient using a fake tinytuya device (no network, no HA)."""

from __future__ import annotations

import asyncio
import time

import pytest

from custom_components.pawbby.const import DP106_COMMANDS
from custom_components.pawbby.pawbby_client import (
    ClientNotConnectedError,
    PawbbyTuyaClient,
)
from custom_components.pawbby.protocol import build_setting_command


class FakeDevice:
    """Minimal stand-in for tinytuya.Device."""

    def __init__(self, messages=(), fail_status=False, fail_receive=False):
        self.messages = list(messages)
        self.fail_status = fail_status
        self.fail_receive = fail_receive
        self.set_calls: list[tuple[int, str, bool]] = []
        self.status_calls = 0
        self.closed = False

    def status(self, nowait=False):
        if self.fail_status:
            raise ConnectionError("unreachable")
        self.status_calls += 1
        return {"dps": {"116": "work_idle"}}

    def receive(self):
        if self.fail_receive:
            raise ConnectionError("socket reset")
        if self.messages:
            return self.messages.pop(0)
        time.sleep(0.005)
        return None

    def set_value(self, index, value, nowait=False):
        self.set_calls.append((index, value, nowait))
        return True

    def close(self):
        self.closed = True


def make_client(fake, **kwargs):
    kwargs.setdefault("device_id", "dev123")
    kwargs.setdefault("local_key", "key")
    kwargs.setdefault("device_factory", lambda **kw: fake)
    kwargs.setdefault("reconnect_delay", 0.01)
    kwargs.setdefault("error_reconnect_delay", 0.01)
    kwargs.setdefault("heartbeat_interval", 0.02)
    kwargs.setdefault("account_sync_delay", 0.01)
    return PawbbyTuyaClient(**kwargs)


# --- message dedup -------------------------------------------------------------


def test_dedup_identical_messages():
    async def run():
        seen = []
        client = make_client(FakeDevice(), on_data=lambda m: seen.append(m))
        msg = {"dps": {"116": "work_idle"}, "t": 123}
        await client._handle_message(msg)
        await client._handle_message(dict(msg))  # identical, within window -> dropped
        assert len(seen) == 1
        await asyncio.sleep(2.1)
        await client._handle_message(dict(msg))  # same payload, window expired -> forwarded
        assert len(seen) == 2
        await client._handle_message({"no": "dps"})  # no dps -> ignored entirely
        assert len(seen) == 2

    asyncio.run(run())


def test_dedup_window_configurable():
    async def run():
        seen = []
        client = make_client(FakeDevice(), on_data=lambda m: seen.append(m), dedup_window=0.05)
        msg = {"dps": {"112": 6000}}
        await client._handle_message(msg)
        await asyncio.sleep(0.1)
        await client._handle_message(msg)
        assert len(seen) == 2

    asyncio.run(run())


# --- commands -------------------------------------------------------------------


def test_send_action_uses_dp106_nowait():
    async def run():
        fake = FakeDevice()
        client = make_client(fake)
        client._device = fake
        client._connected = True
        await client.send_action("clean")
        assert fake.set_calls == [(106, DP106_COMMANDS["clean"], True)]
        await client.send_action("tare" if False else "flatten")
        assert fake.set_calls[-1] == (106, DP106_COMMANDS["flatten"], True)

    asyncio.run(run())


def test_send_action_rejects_unknown():
    async def run():
        client = make_client(FakeDevice())
        client._device = FakeDevice()
        client._connected = True
        with pytest.raises(ValueError, match="Unknown action"):
            await client.send_action("explode")

    asyncio.run(run())


def test_send_setting_and_tare():
    async def run():
        fake = FakeDevice()
        client = make_client(fake)
        client._device = fake
        client._connected = True
        await client.send_setting(build_setting_command("auto_clean", True))
        await client.tare()
        assert fake.set_calls == [
            (105, "AQAAAQE=", True),
            (109, "AQEAAA==", True),
        ]

    asyncio.run(run())


def test_commands_fail_when_not_connected():
    async def run():
        client = make_client(FakeDevice())
        with pytest.raises(ClientNotConnectedError):
            await client.send_action("clean")
        with pytest.raises(ClientNotConnectedError):
            await client.tare()
        with pytest.raises(ClientNotConnectedError):
            await client.refresh_status()

    asyncio.run(run())


# --- account sync ---------------------------------------------------------------


def test_sync_account_settings_utc_kg():
    async def run():
        fake = FakeDevice()
        client = make_client(fake, timezone_name="UTC", weight_unit="kg")
        client._device = fake
        client._connected = True
        await client.sync_account_settings()
        # UTC offset 0 -> 01 09 00 01 00 ; kg -> 01 0A 00 01 00
        assert fake.set_calls == [(105, "AQkAAQA=", True), (105, "AQoAAQA=", True)]

    asyncio.run(run())


def test_sync_account_settings_lb_dst():
    async def run():
        fake = FakeDevice()
        client = make_client(fake, timezone_name="Europe/Berlin", weight_unit="lb")
        client._device = fake
        client._connected = True
        with pytest.MonkeyPatch.context() as mp:
            # pretend it is July -> Europe/Berlin is UTC+2 -> 01 09 00 01 02
            mp.setattr(
                "custom_components.pawbby.pawbby_client.utc_offset_hours_for",
                lambda tz, at=None: 2,
            )
            await client.sync_account_settings()
        payloads = [c[1] for c in fake.set_calls]
        assert payloads == ["AQkAAQI=", "AQoAAQE="]  # UTC+2, lb

    asyncio.run(run())


# --- connect / reconnect loop ----------------------------------------------------


def test_listen_loop_connects_and_forwards_data():
    async def run():
        messages = [{"dps": {"116": "work_idle"}}, {"dps": {"116": "cat_enter"}}]
        fake = FakeDevice(messages=messages)
        received = []
        connected = asyncio.Event()

        def on_data(m):
            received.append(m)

        def on_connected():
            connected.set()

        client = make_client(fake, on_data=on_data, on_connected=on_connected)
        await client.start()
        await asyncio.wait_for(connected.wait(), timeout=2)
        assert client.is_connected
        deadline = time.monotonic() + 2
        while len(received) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await client.stop()
        assert [m["dps"]["116"] for m in received] == ["work_idle", "cat_enter"]
        assert fake.closed

    asyncio.run(run())


def test_reconnect_after_socket_error():
    async def run():
        flaky = FakeDevice(fail_receive=True)   # first device dies on receive
        good = FakeDevice()                      # second device stays alive
        devices = iter([flaky, good])
        disconnected = asyncio.Event()
        factory_calls = []

        def factory(**kw):
            factory_calls.append(kw)
            return next(devices)

        client = make_client(
            None,
            device_factory=factory,
            on_disconnected=lambda exc: disconnected.set(),
        )
        await client.start()
        await asyncio.wait_for(disconnected.wait(), timeout=2)
        deadline = time.monotonic() + 2
        while len(factory_calls) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await client.stop()
        assert len(factory_calls) == 2  # reconnected with a fresh device
        assert factory_calls[0]["device_id"] == "dev123"
        assert flaky.closed

    asyncio.run(run())


def test_connect_failure_uses_error_delay_and_retries():
    async def run():
        attempts = []

        def factory(**kw):
            attempts.append(1)
            if len(attempts) < 3:
                return FakeDevice(fail_status=True)  # status() raises -> connect fails
            return FakeDevice()

        connected = asyncio.Event()
        client = make_client(
            None,
            device_factory=factory,
            on_connected=lambda: connected.set(),
            error_reconnect_delay=0.01,
        )
        await client.start()
        await asyncio.wait_for(connected.wait(), timeout=2)
        await client.stop()
        assert len(attempts) >= 3

    asyncio.run(run())


def test_heartbeat_and_account_sync_after_connect():
    async def run():
        fake = FakeDevice()
        connected = asyncio.Event()
        client = make_client(
            fake,
            timezone_name="UTC",
            weight_unit="kg",
            on_connected=lambda: connected.set(),
            heartbeat_interval=0.02,
            account_sync_delay=0.01,
        )
        await client.start()
        await asyncio.wait_for(connected.wait(), timeout=2)
        # wait for heartbeat ticks + the delayed account sync (tz + unit on DP 105)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if fake.status_calls >= 3 and len(fake.set_calls) >= 2:
                break
            await asyncio.sleep(0.01)
        await client.stop()
        assert fake.status_calls >= 3  # initial connect + heartbeats
        payloads = [c[1] for c in fake.set_calls]
        assert payloads == ["AQkAAQA=", "AQoAAQA="]  # UTC offset 0, kg

    asyncio.run(run())
