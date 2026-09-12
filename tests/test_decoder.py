"""The decoder against the messages the cooler really sent (``tests/captured.py``)."""

from typing import Any

import captured
import pytest

from frostlog import records
from frostlog.cooler import registry
from frostlog.cooler.everfrost.decoder import EverfrostDecoder, decode_state
from frostlog.cooler.everfrost.protocol import ProtocolError, build_frame

DEVICE_INFO = bytes.fromhex(captured.HANDSHAKE_PLAIN)


def _state(message: captured.Message) -> records.CoolerPayload:
    return decode_state(bytes.fromhex(message.plain))


def test_state_report_while_the_battery_runs_the_compressor() -> None:
    payload = _state(captured.DISCHARGING)
    assert payload.setpoint_celsius == -20
    assert payload.interior_temperature_celsius == -17
    assert payload.display_unit == "C"
    assert payload.battery_state == "discharging"
    assert payload.state_of_charge_percent == 80
    assert (payload.charge_watts, payload.discharge_watts) == (0, 43)
    assert payload.input_watts == 0
    assert payload.protection_level == "M" and payload.brightness == "low"
    assert payload.serial_number == captured.SERIAL_NUMBER
    assert payload.battery_serial_number == captured.BATTERY_SERIAL_NUMBER


def test_state_report_on_the_car_socket() -> None:
    payload = _state(captured.CHARGING)
    assert payload.battery_state == "charging"
    assert (payload.input_watts, payload.charge_watts) == (94, 47)
    assert payload.state_of_charge_percent == 73


def test_charging_finished_is_its_own_state() -> None:
    # 100 %, on external power, no current either way: the cooler's fifth state byte.
    payload = _state(captured.FULL)
    assert payload.battery_state == "full"
    assert payload.state_of_charge_percent == 100
    assert (payload.charge_watts, payload.discharge_watts) == (0, 0)


def test_fahrenheit_display_is_normalized_to_celsius() -> None:
    payload = _state(captured.FAHRENHEIT)
    assert payload.display_unit == "F"
    assert payload.setpoint_celsius == -20  # -4 °F
    assert payload.interior_temperature_celsius == -20


def test_battery_out_of_the_cooler_has_no_serial_number() -> None:
    payload = _state(captured.BATTERY_ABSENT)
    assert payload.battery_state == "absent"
    assert payload.state_of_charge_percent == 0
    assert payload.battery_serial_number is None
    assert payload.serial_number == captured.SERIAL_NUMBER  # the cooler is still itself


def test_every_captured_state_report_decodes() -> None:
    for message in captured.STATE_REPORTS:
        payload = _state(message)
        assert -40 <= payload.interior_temperature_celsius <= 85
        assert 0 <= payload.state_of_charge_percent <= 100


def test_a_body_that_is_not_a_state_report_is_refused() -> None:
    with pytest.raises(ProtocolError):
        decode_state(DEVICE_INFO)  # the handshake reply: no battery parameter
    with pytest.raises(ProtocolError):
        decode_state(b"\xa1\x05\xaa")  # truncated


def test_a_parameter_with_no_value_is_refused_rather_than_indexed() -> None:
    # b0 00: the display unit announced, then nothing. Reading its last byte would
    # raise IndexError, which nobody catches, and the collector would exit on a
    # message it had not yet recorded.
    plain = bytearray.fromhex(captured.DISCHARGING.plain)
    at = plain.index(b"\xb0\x02")
    plain[at : at + 4] = b"\xb0\x00"
    with pytest.raises(ProtocolError, match="parameter b0 carries no value"):
        decode_state(bytes(plain))


def test_an_unknown_code_is_refused_rather_than_guessed() -> None:
    plain = bytearray.fromhex(captured.DISCHARGING.plain)
    plain[plain.index(b"\xb0\x02\x01") + 3] = 0x09  # a display unit that is neither °C nor °F
    with pytest.raises(ProtocolError, match="unknown parameter b0"):
        decode_state(bytes(plain))


# --- the exploratory decoder (`frostlog decode`) --------------------------------------


def _record(frames: list[str], **fields: Any) -> records.Cooler:
    return records.cooler("everfrost", "AA:BB", frames, **fields)


def _frames(*payloads: bytes) -> list[str]:
    return [build_frame(bytes.fromhex("030001"), b"\x08\x29", p).hex() for p in payloads]


def test_decode_device_info_from_the_recorded_notification() -> None:
    decoded = EverfrostDecoder().decode(_record(_frames(DEVICE_INFO)))
    assert decoded["source"] == "frames" and decoded["prefix"] is True
    assert decoded["params"]["a2"]["ascii"] == "ESP32"
    assert decoded["params"]["a4"]["ascii"] == captured.SERIAL_NUMBER
    assert decoded["params"]["a1"]["uint_le"] == 3


def test_decode_prefers_the_body_in_the_clear_and_reports_errors() -> None:
    decoded = EverfrostDecoder().decode(_record(_frames(b"\xff"), plain="a10105"))
    assert decoded["source"] == "plain" and decoded["params"]["a1"]["uint_le"] == 5
    assert "error" in EverfrostDecoder().decode(_record(_frames(b"\xa1\x05\xaa")))
    assert "error" in EverfrostDecoder().decode(_record(["ff09"]))  # not a frame
    assert "error" in EverfrostDecoder().decode(_record([]))


def test_fragments_are_joined_before_decoding() -> None:
    first, second = b"\x12" + DEVICE_INFO[:20], b"\x22" + DEVICE_INFO[20:]
    decoded = EverfrostDecoder().decode(_record(_frames(first, second)))
    assert decoded["params"]["a2"]["ascii"] == "ESP32"


def test_typed_readings() -> None:
    decoded = EverfrostDecoder().decode(_record([], plain="a1050500002041"))
    assert decoded["params"]["a1"]["f32le"] == 10.0


def test_registry_decoder() -> None:
    assert registry.create_decoder("everfrost").model == "everfrost"
