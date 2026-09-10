from frostlog.cooler import registry
from frostlog.cooler.everfrost.decoder import EverfrostDecoder
from frostlog.cooler.everfrost.protocol import build_frame

DEVICE_INFO = bytes.fromhex(
    "00a10103a2054553503332a307302e302e302e33a41041504339464530453237333030323735a506f49d8a104e0c"
)


def _frames(*payloads: bytes) -> list[str]:
    return [build_frame(bytes.fromhex("030001"), b"\x08\x29", p).hex() for p in payloads]


def test_decode_device_info_from_the_recorded_notification() -> None:
    payload = {"pattern": "030001", "cmd": "0829", "frames": _frames(DEVICE_INFO)}
    decoded = EverfrostDecoder().decode(payload)
    assert decoded["source"] == "frames" and decoded["prefix"] is True
    assert decoded["params"]["a2"]["ascii"] == "ESP32"
    assert decoded["params"]["a4"]["ascii"] == "APC9FE0E27300275"
    assert decoded["params"]["a1"]["uint_le"] == 3


def test_decode_prefers_plain_and_reports_errors() -> None:
    decoded = EverfrostDecoder().decode(
        {"frames": _frames(b"\xff"), "plain": "a10105", "plain_verified": True}
    )
    assert decoded["source"] == "plain" and decoded["params"]["a1"]["uint_le"] == 5
    assert "error" in EverfrostDecoder().decode({"frames": _frames(b"\xa1\x05\xaa")})
    assert "error" in EverfrostDecoder().decode({"frames": ["ff09"]})  # not a frame
    assert "error" in EverfrostDecoder().decode({})


def test_fragments_are_joined_before_decoding() -> None:
    first, second = b"\x12" + DEVICE_INFO[:20], b"\x22" + DEVICE_INFO[20:]
    decoded = EverfrostDecoder().decode({"frames": _frames(first, second)})
    assert decoded["params"]["a2"]["ascii"] == "ESP32"


def test_unverified_plain_is_not_trusted() -> None:
    record = {"frames": _frames(b"\xa1\x01\x05"), "plain": "ffff", "plain_verified": False}
    decoded = EverfrostDecoder().decode(record)
    assert decoded["source"] == "frames" and decoded["params"]["a1"]["uint_le"] == 5
    assert "note" in decoded


def test_typed_readings() -> None:
    decoded = EverfrostDecoder().decode({"plain": "a1050500002041", "plain_verified": True})
    assert decoded["params"]["a1"]["f32le"] == 10.0


def test_registry_decoder() -> None:
    assert registry.create_decoder("everfrost").model == "everfrost"
