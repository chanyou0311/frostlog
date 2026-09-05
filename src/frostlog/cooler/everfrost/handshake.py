"""Negotiating an encrypted session with an Anker device, as I/O-free state machines.

Two variants exist. Solix power stations (and, we expect, the EverFrost) use
commands ``0001``..``0021`` in the clear, exchange P-256 public keys, and then
switch to AES-CBC with the ECDH secret. Anker Prime chargers use ``4001``..
``4027`` under AES-GCM with a static key, then the ECDH secret. Both sides use
fixed client keys taken from flip-dots/SolixBLE, so the device's public key is
all that is needed to decrypt a session later.

The state machine is fed inbound frames and returns the frames to send back.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from frostlog.cooler.everfrost.protocol import (
    DEFAULT_MTU,
    PATTERN_COMMAND,
    PATTERN_NEGOTIATION,
    CbcCipher,
    Frame,
    GcmCipher,
    Parameter,
    build_frame,
    build_parameters,
    parse_parameters,
    public_key_xy,
    shared_secret,
)

SOLIX_PRIVATE_KEY = bytes.fromhex(
    "7dfbea61cd95cee49c458ad7419e817f1ade9a66136de3c7d5787af1458e39f4"
)
SOLIX_CLIENT_UUID = "b2dc0b17-b75d-4abf-ba6e-ec7c997c23e7"

PRIME_PRIVATE_KEY = bytes.fromhex(
    "754744d72984c378bc4fa77d7fcdf6bbb6d9df119fa9be4948eb8a3b4cd6071f"
)
PRIME_CLIENT_UUID = "79ebed35-dc9c-4904-b40c-72c4e863aa10"
PRIME_NEGOTIATION_KEY = bytes.fromhex("b8ff7422955d4eb6d554a2c470280559")
PRIME_NEGOTIATION_NONCE = bytes.fromhex("6ba3e3f2f3a60f2971ce5d1f")
PRIME_AAD = bytes.fromhex("3322110077665544bbaa9988ffeeddcc")

# What the client asks for at stage 1 of either variant (MTU negotiation).
_MTU_REQUEST = [Parameter(0xA3, None, b"\x20"), Parameter(0xA4, None, b"\x00\xf0")]


class HandshakeError(Exception):
    """The device answered in a way the fixed-key negotiation cannot continue from."""


class Cipher(Protocol):
    def encrypt(self, plain: bytes) -> bytes: ...

    def decrypt(self, payload: bytes) -> tuple[bytes, bool]: ...


@dataclass(frozen=True)
class DeviceInfo:
    chip: str | None = None
    firmware: str | None = None
    serial: str | None = None


def local_posix_tz() -> str:
    """The local zone as a POSIX TZ string such as ``JST-9`` (sign is inverted there)."""
    return f"{time.tzname[0]}{time.timezone // 3600:d}"


def _timestamp_bytes(timestamp: int) -> bytes:
    return timestamp.to_bytes(4, "little")


def _text(parameters: dict[int, Parameter], key: int) -> str | None:
    parameter = parameters.get(key)
    return parameter.raw.decode("ascii", errors="replace") if parameter else None


def _require(parameters: dict[int, Parameter], key: int, stage: str) -> Parameter:
    try:
        return parameters[key]
    except KeyError:
        present = ", ".join(f"{k:02x}" for k in parameters)
        raise HandshakeError(f"{stage}: parameter {key:02x} missing (got {present})") from None


class _Base:
    variant: str

    def __init__(self, timestamp: Callable[[], int] | None = None, posix_tz: str | None = None):
        self._timestamp = timestamp or (lambda: int(time.time()))
        self._tz = posix_tz or local_posix_tz()
        self.mtu = DEFAULT_MTU
        self.cipher: Cipher | None = None
        self.secret: bytes | None = None
        self.device = DeviceInfo()
        self.done = False

    def _ts(self, key: int = 0xA1) -> Parameter:
        return Parameter(key, None, _timestamp_bytes(self._timestamp()))

    def _send(
        self, cmd: str, parameters: list[Parameter], pattern: bytes = PATTERN_NEGOTIATION
    ) -> bytes:
        payload = build_parameters(parameters)
        if self.cipher is not None:
            payload = self.cipher.encrypt(payload)
        return build_frame(pattern, bytes.fromhex(cmd), payload)

    def _parameters(self, frame: Frame) -> dict[int, Parameter]:
        payload = frame.payload
        if self.cipher is not None:
            payload, _ = self.cipher.decrypt(payload)
        return parse_parameters(payload)[1]

    def _set_mtu(self, parameters: dict[int, Parameter]) -> None:
        if 0xA2 in parameters:
            self.mtu = int.from_bytes(parameters[0xA2].raw, "little")

    def _read_device(self, parameters: dict[int, Parameter]) -> None:
        self.device = DeviceInfo(
            chip=_text(parameters, 0xA2),
            firmware=_text(parameters, 0xA3),
            serial=_text(parameters, 0xA4),
        )

    def _derive_secret(self, parameters: dict[int, Parameter], private_key: bytes) -> bytes:
        peer = _require(parameters, 0xA1, "stage 5").raw
        if len(peer) != 64:
            raise HandshakeError(f"stage 5: expected a 64-byte public key, got {len(peer)}")
        self.secret = shared_secret(private_key, peer)
        return self.secret


class SolixHandshake(_Base):
    variant = "solix"

    def _ident(self) -> list[Parameter]:
        return [self._ts(), Parameter(0xA2, None, SOLIX_CLIENT_UUID.encode())]

    def start(self) -> list[bytes]:
        return [self._send("0001", self._ident())]

    def handle(self, frame: Frame) -> list[bytes]:
        if frame.pattern != PATTERN_NEGOTIATION:
            return []
        parameters = self._parameters(frame)
        match frame.cmd.hex():
            case "0801":
                return [self._send("0003", self._ident() + _MTU_REQUEST)]
            case "0803":
                self._set_mtu(parameters)
                return [self._send("0029", self._ident())]
            case "0829":
                self._read_device(parameters)
                extra = [*_MTU_REQUEST, Parameter(0xA5, None, b"\x40")]
                return [self._send("0005", self._ident() + extra)]
            case "0805":
                return [
                    self._send("0021", [Parameter(0xA1, None, public_key_xy(SOLIX_PRIVATE_KEY))])
                ]
            case "0821":
                self.cipher = CbcCipher(self._derive_secret(parameters, SOLIX_PRIVATE_KEY))
                self.done = True
                extra = [
                    Parameter(0xA3, None, b"\x20"),
                    Parameter(0xA4, None, b"\x00\x00\x00\x00"),
                    Parameter(0xA5, None, self._tz.encode()),
                ]
                return [self._send("4022", self._ident() + extra)]
            case _:
                return []


class PrimeHandshake(_Base):
    variant = "prime"

    def __init__(self, timestamp: Callable[[], int] | None = None, posix_tz: str | None = None):
        super().__init__(timestamp, posix_tz)
        self.cipher = GcmCipher(PRIME_NEGOTIATION_KEY, PRIME_NEGOTIATION_NONCE, PRIME_AAD)

    def start(self) -> list[bytes]:
        return [self._send("4001", [self._ts()])]

    def handle(self, frame: Frame) -> list[bytes]:
        if frame.pattern != PATTERN_NEGOTIATION:
            return []
        parameters = self._parameters(frame)
        match frame.cmd.hex():
            case "4801":
                return [self._send("4003", [self._ts(), *_MTU_REQUEST])]
            case "4803":
                self._set_mtu(parameters)
                return [self._send("4029", [self._ts()])]
            case "4829":
                self._read_device(parameters)
                extra = [
                    Parameter(0xA3, None, b"\x20"),
                    Parameter(0xA4, None, b"\x29\x01"),
                    Parameter(0xA5, None, b"\x44"),
                    Parameter(0xA6, None, b"\x02"),
                ]
                return [self._send("4005", [self._ts(), *extra])]
            case "4805":
                return [
                    self._send("4021", [Parameter(0xA1, None, public_key_xy(PRIME_PRIVATE_KEY))])
                ]
            case "4821":
                secret = self._derive_secret(parameters, PRIME_PRIVATE_KEY)
                self.cipher = GcmCipher.from_secret(secret, PRIME_AAD)
                extra = [
                    Parameter(0xA3, None, b"\x00\x00\x00\x00"),
                    Parameter(0xA5, None, self._tz.encode()),
                ]
                return [self._send("4022", [self._ts(), *extra])]
            case "4822":
                return [
                    self._send(
                        "4027", [self._ts(), Parameter(0xA2, None, PRIME_CLIENT_UUID.encode())]
                    )
                ]
            case "4827":
                self.done = True
                subscribe = [
                    Parameter(0xA1, None, b"\x21"),
                    Parameter(0xA2, None, bytes.fromhex("044742")),
                    Parameter(0xA3, 4, PRIME_CLIENT_UUID.encode()),
                    Parameter(0xA5, None, b"\x01\x01"),
                    self._ts(0xFE),
                ]
                return [
                    self._send(
                        "4200", [Parameter(0xA1, None, b"\x21"), self._ts(0xFE)], PATTERN_COMMAND
                    ),
                    self._send("420a", subscribe, PATTERN_COMMAND),
                ]
            case _:
                return []
