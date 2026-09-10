"""ASAIR DHT20 (AHT20-compatible) at 0x38.

Trigger a measurement with ``AC 33 00``, wait about 80 ms, then read 7 bytes:
status, 20 bits of humidity, 20 bits of temperature, CRC-8 (poly 0x31, init 0xFF).
"""

import time

from frostlog.ambient.base import Reading, Sensor, SensorError

_TRIGGER = bytes([0xAC, 0x33, 0x00])
_BUSY = 0x80


def crc8(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def decode(frame: bytes) -> Reading:
    if len(frame) != 7:
        raise SensorError(f"DHT20: expected 7 bytes, got {len(frame)}")
    if frame[0] & _BUSY:
        raise SensorError("DHT20: sensor still busy")
    if crc8(frame[:6]) != frame[6]:
        raise SensorError(f"DHT20: CRC mismatch on {frame.hex()}")
    raw_humidity = (frame[1] << 12) | (frame[2] << 4) | (frame[3] >> 4)
    raw_temp = ((frame[3] & 0x0F) << 16) | (frame[4] << 8) | frame[5]
    return Reading(
        temperature_celsius=round(raw_temp / 2**20 * 200 - 50, 2),
        humidity_percent=round(raw_humidity / 2**20 * 100, 2),
    )


class DHT20(Sensor):
    name = "dht20"
    default_address = 0x38

    def read(self) -> Reading:
        try:
            self.bus.write(self.address, _TRIGGER)
            time.sleep(0.08)
            frame = self.bus.read(self.address, 7)
            for _ in range(5):
                if not frame[0] & _BUSY:
                    break
                time.sleep(0.01)
                frame = self.bus.read(self.address, 7)
        except OSError as exc:
            raise SensorError(f"DHT20: I2C error: {exc}") from exc
        return decode(frame)
