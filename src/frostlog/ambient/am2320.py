"""ASAIR AM2320 at 0x5C.

The sensor sleeps between reads: a first write only wakes it (and is not
acknowledged), then ``03 00 04`` asks for the 4 data registers and the reply is
``03 04 <RH hi> <RH lo> <T hi> <T lo> <CRC lo> <CRC hi>`` with CRC-16/MODBUS.
The value returned is the previous measurement, and reads must be at least
2 seconds apart.
"""

import contextlib
import time

from frostlog.ambient.base import Reading, Sensor, SensorError

_READ_REGISTERS = bytes([0x03, 0x00, 0x04])


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def decode(frame: bytes) -> Reading:
    if len(frame) != 8:
        raise SensorError(f"AM2320: expected 8 bytes, got {len(frame)}")
    if frame[0] != 0x03 or frame[1] != 0x04:
        raise SensorError(f"AM2320: unexpected reply header {frame[:2].hex()}")
    if crc16(frame[:6]) != frame[6] | (frame[7] << 8):
        raise SensorError(f"AM2320: CRC mismatch on {frame.hex()}")
    humidity = ((frame[2] << 8) | frame[3]) / 10
    temp = (((frame[4] & 0x7F) << 8) | frame[5]) / 10
    if frame[4] & 0x80:
        temp = -temp
    return Reading(temp_c=temp, humidity_pct=humidity)


class AM2320(Sensor):
    name = "am2320"
    default_address = 0x5C
    min_interval = 2.0

    def read(self) -> Reading:
        try:
            with contextlib.suppress(OSError):  # the wake-up write is not acknowledged
                self.bus.write(self.address, b"\x00")
            time.sleep(0.002)
            self.bus.write(self.address, _READ_REGISTERS)
            time.sleep(0.002)
            frame = self.bus.read(self.address, 8)
        except OSError as exc:
            raise SensorError(f"AM2320: I2C error: {exc}") from exc
        return decode(frame)
