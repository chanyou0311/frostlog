from frostlog.cooler import registry
from frostlog.cooler.everfrost.decoder import EverfrostDecoder


def test_decode_device_info_payload() -> None:
    payload = {
        "pattern": "030001",
        "cmd": "0829",
        "payload": "00a10103a2054553503332a307302e302e302e33a41041504339464530453237333030323735a506f49d8a104e0c",
    }
    decoded = EverfrostDecoder().decode(payload)
    assert decoded["source"] == "payload" and decoded["prefix"] is True
    assert decoded["params"]["a2"]["ascii"] == "ESP32"
    assert decoded["params"]["a4"]["ascii"] == "APC9FE0E27300275"
    assert decoded["params"]["a1"]["uint_le"] == 3


def test_decode_prefers_plain_and_reports_errors() -> None:
    decoded = EverfrostDecoder().decode(
        {"payload": "ff", "plain": "a10105", "plain_verified": True}
    )
    assert decoded["source"] == "plain" and decoded["params"]["a1"]["uint_le"] == 5
    assert "error" in EverfrostDecoder().decode({"payload": "a105aa"})
    assert "error" in EverfrostDecoder().decode({})


def test_unverified_plain_is_not_trusted() -> None:
    record = {"payload": "a10105", "plain": "ffff", "plain_verified": False}
    decoded = EverfrostDecoder().decode(record)
    assert decoded["source"] == "payload" and decoded["params"]["a1"]["uint_le"] == 5
    assert "note" in decoded


def test_typed_readings() -> None:
    decoded = EverfrostDecoder().decode({"plain": "a1050500002041", "plain_verified": True})
    assert decoded["params"]["a1"]["f32le"] == 10.0


def test_registry_decoder() -> None:
    assert registry.create_decoder("everfrost").model == "everfrost"
