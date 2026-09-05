"""Bluetooth only: find the device, connect, subscribe to notifications, write frames."""

import asyncio
import contextlib
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from frostlog.cooler.base import Found
from frostlog.cooler.everfrost.protocol import NOTIFY_CHAR_UUID, SERVICE_UUID, WRITE_CHAR_UUID

__all__ = ["BleakError", "Session", "find_device", "scan"]


async def scan(timeout: float = 10.0) -> list[Found]:
    """Every device seen in ``timeout`` seconds; those advertising Anker's service first."""
    seen = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = [
        Found(
            address=device.address,
            name=device.name,
            rssi=advertisement.rssi,
            cooler=SERVICE_UUID in advertisement.service_uuids,
        )
        for device, advertisement in seen.values()
    ]
    return sorted(found, key=lambda f: (not f.cooler, -f.rssi))


async def find_device(address: str | None, timeout: float) -> BLEDevice | None:
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)
    return await BleakScanner.find_device_by_filter(
        lambda _device, advertisement: SERVICE_UUID in advertisement.service_uuids,
        timeout=timeout,
    )


class Session:
    """One connection: notifications queue up, ``write`` sends a frame, ``disconnected`` is set
    by the stack when the link drops."""

    def __init__(self, device: BLEDevice, connect_timeout: float = 20.0) -> None:
        self.address = device.address
        self.name = device.name
        self.disconnected = asyncio.Event()
        self.dropped = 0
        self._client = BleakClient(
            device, disconnected_callback=self._on_disconnect, timeout=connect_timeout
        )
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4096)

    async def __aenter__(self) -> "Session":
        await self._client.connect()
        await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
        return self

    async def __aexit__(self, *exc: object) -> None:
        with contextlib.suppress(Exception):
            await self._client.disconnect()

    def _on_notify(self, _characteristic: Any, data: bytearray) -> None:
        try:
            self._queue.put_nowait(bytes(data))
        except asyncio.QueueFull:
            self.dropped += 1

    def _on_disconnect(self, _client: BleakClient) -> None:
        self.disconnected.set()

    async def write(self, data: bytes) -> None:
        await self._client.write_gatt_char(WRITE_CHAR_UUID, data)

    async def next_notification(self, timeout: float) -> bytes | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None
