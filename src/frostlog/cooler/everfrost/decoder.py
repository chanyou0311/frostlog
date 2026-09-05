"""Split a recorded payload into its parameters and show each in several readings.

Which parameter means what is not known yet; this is the tool for finding out
by comparing records against the app while changing one thing at a time.
"""

import math
import struct
from typing import Any

from frostlog.cooler.everfrost.protocol import Parameter, ProtocolError, parse_parameters

MODEL = "everfrost"

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

    def decode(self, payload: dict[str, Any]) -> dict[str, Any]:
        # A plaintext whose tag did not verify is most likely the wrong key's output
        # (the Prime fallback decrypts everything with a static key): use the raw bytes.
        verified = payload.get("plain_verified", True)
        source = "plain" if payload.get("plain") and verified else "payload"
        hex_payload = payload.get(source)
        if not hex_payload:
            return {"error": "no payload to decode"}
        out: dict[str, Any] = {"source": source}
        if not verified:
            out["note"] = "plain did not verify; decoding the raw payload"
        try:
            prefix, parameters = parse_parameters(bytes.fromhex(hex_payload))
        except (ProtocolError, ValueError) as exc:
            return out | {"error": str(exc)}
        return out | {
            "prefix": prefix,
            "params": {f"{key:02x}": describe(parameter) for key, parameter in parameters.items()},
        }
