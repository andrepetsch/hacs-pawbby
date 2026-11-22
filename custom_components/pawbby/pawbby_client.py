"""Async Tuya client wrapper for the Pawbby litter box.

Wraps a blocking ``tinytuya.Device`` in an asyncio-friendly client with
reconnect/backoff, heartbeat, account sync and message dedup — a port of the
connection management in Pawbby-Reborn's ``web/server/plugins/tuya-listener.ts``.

Home Assistant imports are deliberately avoided here so the client can be
unit-tested with a fake device (see tests/test_client.py); the HA glue
(coordinator, config flow) lives in later phases.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import time
from typing import Any, Callable

from .const import (
    DP_CLEAN_CONTROL,
    DP_DEVICE_GATE,
    DP_TARE,
    DP106_COMMANDS,
    DP109_TARE_PAYLOAD,
    TUYA_PROTOCOL_VERSION,
)
from .protocol import SettingCommand, build_setting_command, utc_offset_hours_for

_LOGGER = logging.getLogger(__name__)

# Timings ported from tuya-listener.ts
DEFAULT_RECONNECT_DELAY = 5.0          # after a mid-connection drop ("disconnected")
DEFAULT_ERROR_RECONNECT_DELAY = 10.0   # after a connect/socket failure
DEFAULT_HEARTBEAT_INTERVAL = 300.0     # keepalive status refresh every 5 min
DEFAULT_ACCOUNT_SYNC_DELAY = 3.0       # time-zone push 3 s after every connect
DEFAULT_ACCOUNT_SYNC_COOLDOWN = 600.0  # ... but at most every 10 min
DEFAULT_DEDUP_WINDOW = 2.0             # identical payloads within 2 s are dropped

DeviceFactory = Callable[..., Any]


class ClientNotConnectedError(RuntimeError):
    """Raised when a command is sent while the box is not connected."""


def _default_device_factory(
    device_id: str, ip: str | None, local_key: str, version: str
) -> Any:
    """Build a persistent tinytuya device (discovering the IP if not given)."""
    import tinytuya

    address = ip
    if address is None:
        found = tinytuya.find_device(dev_id=device_id)
        if not found:
            raise ConnectionError(f"Tuya device {device_id} not found on the network")
        address = found["ip"]
    device = tinytuya.Device(
        device_id,
        address,
        local_key,
        version=float(version),
        persist=True,  # keep the socket open so the box can push DPs
    )
    device.set_socketRetryLimit(3)
    return device


class PawbbyTuyaClient:
    """Connection-managed wrapper around one litter box.

    Callbacks (all optional, sync or async):
      - ``on_data(message)``: raw tinytuya message dict carrying ``dps``
      - ``on_connected()`` / ``on_disconnected(exc)``
    """

    def __init__(
        self,
        *,
        device_id: str,
        local_key: str,
        ip: str | None = None,
        timezone_name: str = "UTC",
        weight_unit: str = "kg",
        device_factory: DeviceFactory | None = None,
        on_data: Callable | None = None,
        on_connected: Callable | None = None,
        on_disconnected: Callable | None = None,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        error_reconnect_delay: float = DEFAULT_ERROR_RECONNECT_DELAY,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        account_sync_delay: float = DEFAULT_ACCOUNT_SYNC_DELAY,
        account_sync_cooldown: float = DEFAULT_ACCOUNT_SYNC_COOLDOWN,
        dedup_window: float = DEFAULT_DEDUP_WINDOW,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._device_id = device_id
        self._local_key = local_key
        self._ip = ip
        self._version = TUYA_PROTOCOL_VERSION
        self._timezone_name = timezone_name
        self._weight_unit = "kg" if weight_unit not in ("kg", "lb") else weight_unit
        self._device_factory = device_factory or _default_device_factory
        self._on_data = on_data
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._reconnect_delay = reconnect_delay
        self._error_reconnect_delay = error_reconnect_delay
        self._heartbeat_interval = heartbeat_interval
        self._account_sync_delay = account_sync_delay
        self._account_sync_cooldown = account_sync_cooldown
        self._dedup_window = dedup_window

        self._device: Any = None
        self._connected = False
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._account_sync_task: asyncio.Task | None = None
        self._last_key: str | None = None
        self._last_time = 0.0
        # -inf (not 0.0): time.monotonic() is seconds since BOOT, so on a freshly
        # booted machine 0.0 would look like "synced recently" and skip the sync.
        self._last_account_sync = float("-inf")
        self._monotonic = monotonic

    # ------------------------------------------------------------------ state

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def is_connected(self) -> bool:
        return self._connected and self._device is not None

    # ------------------------------------------------------------- life cycle

    async def start(self) -> None:
        """Start the background listen/reconnect loop (idempotent)."""
        if self._listen_task is not None and not self._listen_task.done():
            return
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        """Stop the loop and close the socket."""
        self._running = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        await self._close_device()

    async def _close_device(self) -> None:
        device, self._device = self._device, None
        self._connected = False
        if device is not None:
            try:
                await asyncio.get_running_loop().run_in_executor(None, device.close)
            except Exception:
                pass

    def _connect(self) -> Any:
        """Blocking: create the device and establish the session (executor)."""
        device = self._device_factory(
            device_id=self._device_id,
            ip=self._ip,
            local_key=self._local_key,
            version=self._version,
        )
        device.status()  # raises if the box is unreachable
        return device

    async def _listen_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            delay = self._error_reconnect_delay
            device = None
            try:
                device = await loop.run_in_executor(None, self._connect)
                self._device = device
                self._connected = True
                self._last_key = None
                _LOGGER.info("Connected to Pawbby box %s", self._device_id)
                delay = self._reconnect_delay  # once connected, drops use the short delay
                await self._fire(self._on_connected)
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                self._account_sync_task = asyncio.create_task(self._delayed_account_sync())
                while self._running:
                    msg = await loop.run_in_executor(None, device.receive)
                    if msg:
                        await self._handle_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("Connection to %s lost: %s", self._device_id, exc)
                await self._fire(self._on_disconnected, exc)
            finally:
                for task in (self._heartbeat_task, self._account_sync_task):
                    if task is not None:
                        task.cancel()
                self._heartbeat_task = self._account_sync_task = None
                await self._close_device()
            if self._running:
                _LOGGER.info("Reconnecting to %s in %.0fs...", self._device_id, delay)
                await asyncio.sleep(delay)

    # ---------------------------------------------------------------- messages

    async def _handle_message(self, msg: Any) -> None:
        """Dedup identical payloads (the box re-sends 'data' and 'dp-refresh')."""
        key = json.dumps(msg, sort_keys=True, default=str)
        now = self._monotonic()
        if key == self._last_key and now - self._last_time < self._dedup_window:
            return
        self._last_key = key
        self._last_time = now
        if isinstance(msg, dict) and msg.get("dps"):
            await self._fire(self._on_data, msg)

    async def _heartbeat_loop(self) -> None:
        """Keepalive: a full status refresh every heartbeat_interval (5 min)."""
        loop = asyncio.get_running_loop()
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)
            device = self._device
            if device is None:
                return
            try:
                await loop.run_in_executor(None, device.status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The receive loop will notice the dead socket and reconnect.
                _LOGGER.debug("Heartbeat to %s failed: %s", self._device_id, exc)

    async def _delayed_account_sync(self) -> None:
        """Push time zone + weight unit shortly after connect (10-min cooldown).

        Mirrors the vendor app / Pawbby-Reborn daemon: the time-zone push also
        makes the box emit a fresh DP 103 settings snapshot within ~1 s.
        """
        await asyncio.sleep(self._account_sync_delay)
        now = self._monotonic()
        if now - self._last_account_sync < self._account_sync_cooldown:
            return
        self._last_account_sync = now
        try:
            await self.sync_account_settings()
        except Exception as exc:
            _LOGGER.warning("Account sync to %s failed: %s", self._device_id, exc)

    # ---------------------------------------------------------------- commands

    async def _set_dp(self, dp: int, payload: str) -> None:
        device = self._device
        if not self.is_connected:
            raise ClientNotConnectedError(f"Device {self._device_id} is not connected")
        _LOGGER.debug("DP %s = %s (%s)", dp, payload, self._device_id)
        await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(device.set_value, dp, payload, nowait=True)
        )

    async def send_action(self, action: str) -> None:
        """Send a motor command (clean/flatten/empty/cancel_clean) on DP 106."""
        if action not in DP106_COMMANDS:
            raise ValueError(
                f"Unknown action {action!r}. Allowed: {', '.join(sorted(DP106_COMMANDS))}"
            )
        await self._set_dp(DP_CLEAN_CONTROL, DP106_COMMANDS[action])

    async def send_setting(self, command: SettingCommand) -> None:
        """Send a DP 105 settings command (from protocol.build_setting_command)."""
        await self._set_dp(DP_DEVICE_GATE, command.payload)

    async def tare(self) -> None:
        """Zero the scale (DP 109)."""
        await self._set_dp(DP_TARE, DP109_TARE_PAYLOAD)

    async def refresh_status(self) -> None:
        """Request a full status refresh (same call the heartbeat uses)."""
        device = self._device
        if not self.is_connected:
            raise ClientNotConnectedError(f"Device {self._device_id} is not connected")
        await asyncio.get_running_loop().run_in_executor(None, device.status)

    async def sync_account_settings(
        self, *, timezone: bool = True, weight_unit: bool = True
    ) -> None:
        """Mirror the HA instance's time zone / weight unit onto the box."""
        if timezone:
            offset = utc_offset_hours_for(self._timezone_name)
            await self.send_setting(build_setting_command("sync_timezone", offset))
        if weight_unit:
            await self.send_setting(build_setting_command("weight_unit", self._weight_unit))

    # ------------------------------------------------------------------ utils

    async def _fire(self, cb: Callable | None, *args: Any) -> None:
        if cb is None:
            return
        result = cb(*args)
        if inspect.isawaitable(result):
            await result
