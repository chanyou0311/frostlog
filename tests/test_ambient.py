import pytest

from frostlog import records
from frostlog.ambient import am2320, dht20, registry
from frostlog.ambient.base import SensorError
from frostlog.ambient.reader import read_loop


class FakeBus:
    def __init__(self, replies: list[bytes]) -> None:
        self.replies = list(replies)
        self.writes: list[tuple[int, bytes]] = []

    def write(self, address: int, data: bytes) -> None:
        self.writes.append((address, data))

    def read(self, address: int, length: int) -> bytes:
        reply = self.replies.pop(0)
        assert len(reply) == length
        return reply


def test_crc8_check_value() -> None:
    # CRC-8/NRSC-5 (poly 0x31, init 0xFF): the standard check value for "123456789".
    assert dht20.crc8(b"123456789") == 0xF7


def test_crc16_check_value() -> None:
    # CRC-16/MODBUS: the standard check value for "123456789".
    assert am2320.crc16(b"123456789") == 0x4B37


def _dht20_frame(raw_humidity: int, raw_temp: int, status: int = 0x1C) -> bytes:
    body = bytes(
        [
            status,
            raw_humidity >> 12 & 0xFF,
            raw_humidity >> 4 & 0xFF,
            (raw_humidity & 0x0F) << 4 | raw_temp >> 16 & 0x0F,
            raw_temp >> 8 & 0xFF,
            raw_temp & 0xFF,
        ]
    )
    return body + bytes([dht20.crc8(body)])


def test_dht20_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    # 50 % RH and 25 °C: raw = 2^19 and raw = (25 + 50) / 200 * 2^20.
    bus = FakeBus([_dht20_frame(2**19, int(75 / 200 * 2**20))])
    reading = dht20.DHT20(bus).read()
    assert bus.writes == [(0x38, bytes([0xAC, 0x33, 0x00]))]
    assert reading.humidity_pct == pytest.approx(50.0, abs=0.01)
    assert reading.temp_c == pytest.approx(25.0, abs=0.01)


def test_dht20_bad_crc() -> None:
    frame = bytearray(_dht20_frame(2**19, 2**19))
    frame[6] ^= 0xFF
    with pytest.raises(SensorError):
        dht20.decode(bytes(frame))


def _am2320_frame(humidity_x10: int, temp_x10: int) -> bytes:
    sign = 0x80 if temp_x10 < 0 else 0
    temp_x10 = abs(temp_x10)
    body = bytes(
        [0x03, 0x04, humidity_x10 >> 8, humidity_x10 & 0xFF, sign | temp_x10 >> 8, temp_x10 & 0xFF]
    )
    crc = am2320.crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def test_am2320_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    bus = FakeBus([_am2320_frame(452, -105)])
    reading = am2320.AM2320(bus, 0x5C).read()
    assert bus.writes == [(0x5C, b"\x00"), (0x5C, bytes([0x03, 0x00, 0x04]))]
    assert reading.humidity_pct == pytest.approx(45.2)
    assert reading.temp_c == pytest.approx(-10.5)


def test_am2320_ignores_wakeup_nack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)

    class NackBus(FakeBus):
        def write(self, address: int, data: bytes) -> None:
            super().write(address, data)
            if data == b"\x00":
                raise OSError(121, "Remote I/O error")

    reading = am2320.AM2320(NackBus([_am2320_frame(500, 250)])).read()
    assert reading.temp_c == pytest.approx(25.0)


def test_registry() -> None:
    assert registry.create("am2320", FakeBus([]), None).address == 0x5C
    assert registry.create("dht20", FakeBus([]), 0x39).address == 0x39
    with pytest.raises(ValueError):
        registry.create("nope", FakeBus([]))


def test_read_loop_emits_records_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    good = _dht20_frame(2**19, 2**19)
    bad = bytes(7)
    sensor = dht20.DHT20(FakeBus([good, bad, good]))
    slept: list[float] = []
    out = list(read_loop(sensor, interval=10, count=3, sleep=slept.append))
    assert [r.type for r in out] == ["ambient", "event", "ambient"]
    assert isinstance(out[1], records.Event) and out[1].kind == "ambient_read_failed"
    # sleep is a no-op here, so the grid keeps stretching: ~10 s, then ~20 s.
    assert [round(s) for s in slept] == [10, 20]


def test_read_loop_reanchors_after_a_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr("frostlog.ambient.reader.clock.uptime", lambda: clock["now"])
    frames = [_dht20_frame(2**19, 2**19)] * 4
    sensor = dht20.DHT20(FakeBus(frames))
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds
        if len(slept) == 1:
            clock["now"] += 100  # the Pi stalled for 100 s after the first sleep

    out = list(read_loop(sensor, interval=10, count=4, sleep=sleep))
    assert len([r for r in out if r.type == "ambient"]) == 4
    # 10 s to the second read; the stall is not "caught up" with back-to-back reads:
    # the grid restarts from the moment the loop woke up.
    assert [round(s) for s in slept] == [10, 10]
