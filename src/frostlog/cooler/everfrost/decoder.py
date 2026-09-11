"""What an EverFrost message means.

:func:`decode_state` reads the one message type whose meaning is known, the
state report (cmd ``4402``): the cooler sends it on every value change and as a
heartbeat, and everything the data product answers comes from it. The byte map
was found by comparing recorded messages with the cooler's display and the
Anker app while changing one thing at a time.

:class:`EverfrostDecoder` is the tool that map was found with and stays for the
next unknown message type: it splits a payload into its parameters and shows
each in several readings, without deciding what any of them mean.
"""

import math
import struct
from typing import Any

from frostlog import records
from frostlog.cooler.everfrost import MODEL
from frostlog.cooler.everfrost.protocol import (
    Parameter,
    ProtocolError,
    parse_parameters,
    payload_from_notifications,
)

#: The state report; the only command whose body is decoded.
CMD_STATE = "4402"

# Parameters of a state report. Values are given after the type byte.
_SERIAL_NUMBER = 0xA2
_SETPOINT = 0xA6
_INTERIOR_TEMPERATURE = 0xA7
_BRIGHTNESS = 0xAD
_PROTECTION_LEVEL = 0xAF
_DISPLAY_UNIT = 0xB0
_INPUT_WATTS = 0xB1
_BATTERY = 0xD0

# Offsets into the battery parameter, counted from its type byte.
_BATTERY_SERIAL_NUMBER = slice(3, 20)
_USB_A_OUTPUT_WATTS = 24
_USB_C_OUTPUT_WATTS = 26
_BATTERY_STATE = 30
_STATE_OF_CHARGE = 31
_CHARGE_WATTS = 32
_DISCHARGE_WATTS = 34
_BATTERY_LENGTH = 36

_DISPLAY_UNITS = {0: "C", 1: "F"}
_BATTERY_STATES = {0: "idle", 1: "charging", 2: "discharging", 3: "full", 4: "absent"}
_PROTECTION_LEVELS = {0: "L", 1: "M", 2: "H"}
_BRIGHTNESS_LEVELS = {0: "low", 1: "mid", 2: "high"}


def decode_state(plain: bytes) -> records.CoolerPayload:
    """The decoded body of a state report; raises :class:`ProtocolError` on anything else."""
    _, parameters = parse_parameters(plain)
    battery = _parameter(parameters, _BATTERY).raw
    if len(battery) < _BATTERY_LENGTH:
        raise ProtocolError(f"battery parameter is {len(battery)} bytes: {battery.hex()}")
    unit = _choice(parameters, _DISPLAY_UNIT, _DISPLAY_UNITS)
    serial_number = battery[_BATTERY_SERIAL_NUMBER]
    return records.CoolerPayload(
        setpoint_celsius=_celsius(_signed(parameters, _SETPOINT), unit),
        interior_temperature_celsius=_celsius(_signed(parameters, _INTERIOR_TEMPERATURE), unit),
        display_unit=unit,
        input_watts=_signed(parameters, _INPUT_WATTS),
        usb_a_output_watts=_watts(battery, _USB_A_OUTPUT_WATTS),
        usb_c_output_watts=_watts(battery, _USB_C_OUTPUT_WATTS),
        charge_watts=_watts(battery, _CHARGE_WATTS),
        discharge_watts=_watts(battery, _DISCHARGE_WATTS),
        battery_state=_named(battery[_BATTERY_STATE], _BATTERY_STATES, "battery state"),
        state_of_charge_percent=battery[_STATE_OF_CHARGE],
        protection_level=_choice(parameters, _PROTECTION_LEVEL, _PROTECTION_LEVELS),
        brightness=_choice(parameters, _BRIGHTNESS, _BRIGHTNESS_LEVELS),
        serial_number=_ascii(_parameter(parameters, _SERIAL_NUMBER).value),
        # All zeros while no battery is installed.
        battery_serial_number=_ascii(serial_number) if any(serial_number) else None,
    )


def _parameter(parameters: dict[int, Parameter], key: int) -> Parameter:
    try:
        return parameters[key]
    except KeyError:
        present = ", ".join(f"{k:02x}" for k in parameters)
        raise ProtocolError(f"parameter {key:02x} missing (got {present})") from None


def _signed(parameters: dict[int, Parameter], key: int) -> int:
    return int.from_bytes(_parameter(parameters, key).value, "little", signed=True)


def _choice(parameters: dict[int, Parameter], key: int, names: dict[int, str]) -> Any:
    return _named(_parameter(parameters, key).value[-1], names, f"parameter {key:02x}")


def _named(code: int, names: dict[int, str], what: str) -> Any:
    """The name of a code; ``Any`` because the caller's field is a narrower Literal."""
    try:
        return names[code]
    except KeyError:
        raise ProtocolError(f"unknown {what} {code:#04x}") from None


def _watts(battery: bytes, offset: int) -> int:
    return int.from_bytes(battery[offset : offset + 2], "little")


def _celsius(value: int, unit: str) -> int:
    """The cooler shows whole degrees in ``unit``; °F are converted, °C are already right."""
    return round((value - 32) * 5 / 9) if unit == "F" else value


def _ascii(value: bytes) -> str:
    return value.decode("ascii", errors="replace")


# --- exploring an unknown message type ------------------------------------------------

# Type bytes seen in Anker payloads and the reading they suggest.
_TYPED = {0x00: "str", 0x01: "u8", 0x02: "i16le", 0x04: "bytes", 0x05: "f32le"}


def describe(parameter: Parameter) -> dict[str, Any]:
    raw = parameter.raw
    value = parameter.value
    out: dict[str, Any] = {
        "len": len(raw),
        "hex": raw.hex(),
        "uint_le": int.from_bytes(raw, "little"),
        "int_le": int.from_bytes(raw, "little", signed=True),
    }
    if parameter.type is not None:
        out["type"] = f"{parameter.type:02x}"
        out["value_hex"] = value.hex()
        out["value_uint_le"] = int.from_bytes(value, "little")
        reading = _TYPED.get(parameter.type)
        if reading == "f32le" and len(value) == 4:
            number = struct.unpack("<f", value)[0]
            out["f32le"] = number if math.isfinite(number) else None
        elif reading == "i16le" and len(value) == 2:
            out["i16le"] = struct.unpack("<h", value)[0]
        elif reading == "u8" and len(value) == 1:
            out["u8"] = value[0]
        elif reading == "str":
            out["str"] = value.decode("utf-8", errors="replace")
    if raw and all(32 <= b < 127 for b in raw):
        out["ascii"] = raw.decode("ascii")
    return out


class EverfrostDecoder:
    model = MODEL

    def decode(self, record: records.Cooler) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            if record.plain:
                out["source"] = "plain"
                data = bytes.fromhex(record.plain)
            elif record.frames:
                # Encrypted, or recorded before the session was: the notification bytes
                # are the message as it arrived, which is all there is to look at.
                out["source"] = "frames"
                data = payload_from_notifications([bytes.fromhex(f) for f in record.frames])
            else:
                return {"error": "no message to decode"}
            prefix, parameters = parse_parameters(data)
        except (ProtocolError, ValueError) as exc:
            return out | {"error": str(exc)}
        return out | {
            "prefix": prefix,
            "params": {f"{key:02x}": describe(parameter) for key, parameter in parameters.items()},
        }
