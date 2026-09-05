"""The little of I2C the sensors need: write some bytes, read some bytes."""

from typing import Protocol


class Bus(Protocol):
    def write(self, address: int, data: bytes) -> None: ...

    def read(self, address: int, length: int) -> bytes: ...


class SMBus2Bus:
    """A :class:`Bus` on ``/dev/i2c-<number>`` via smbus2 (imported lazily; Linux only)."""

    def __init__(self, number: int) -> None:
        from smbus2 import SMBus

        self._bus = SMBus(number)

    def write(self, address: int, data: bytes) -> None:
        from smbus2 import i2c_msg

        self._bus.i2c_rdwr(i2c_msg.write(address, list(data)))

    def read(self, address: int, length: int) -> bytes:
        from smbus2 import i2c_msg

        message = i2c_msg.read(address, length)
        self._bus.i2c_rdwr(message)
        return bytes(message)
