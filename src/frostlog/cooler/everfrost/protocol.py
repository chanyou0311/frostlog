"""The Anker BLE wire format: frames, fragments, TLV parameters and the two ciphers.

Everything here is a pure function of bytes; no I/O. The format was learned
from flip-dots/SolixBLE (MIT), whose captures are used as test vectors.

Frame:     ``ff09 <len u16le> <pattern 3B> <cmd 2B> <payload> <xor of all preceding bytes>``
Fragment:  when a message does not fit the MTU, each notification's payload starts
           with one byte ``<index nibble><total nibble>`` (index counts from 1).
Parameter: ``<key 1B> <length 1B> [<type 1B>] <value>``; the type byte is present
           when length > 1. Payloads may start with a lone ``00`` prefix byte.
"""

import functools
from collections.abc import Iterable
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SERVICE_UUID = "0000ff09-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "8c850002-0302-41c5-b46e-cf057c562025"
NOTIFY_CHAR_UUID = "8c850003-0302-41c5-b46e-cf057c562025"

HEADER = b"\xff\x09"
PATTERN_NEGOTIATION = bytes.fromhex("030001")
PATTERN_COMMAND = bytes.fromhex("03000f")
PATTERNS_TELEMETRY = frozenset({bytes.fromhex("03010f"), bytes.fromhex("030111")})

DEFAULT_MTU = 253


class ProtocolError(ValueError):
    """Bytes that do not follow the format."""


# --- frames -------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    pattern: bytes
    cmd: bytes
    payload: bytes

    @property
    def key(self) -> bytes:
        return self.pattern + self.cmd


def xor_checksum(data: bytes) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result


def parse_frame(data: bytes) -> Frame:
    if len(data) < 10 or data[:2] != HEADER:
        raise ProtocolError(f"not a frame: {data.hex()}")
    length = int.from_bytes(data[2:4], "little")
    if length != len(data):
        raise ProtocolError(f"length field {length} but {len(data)} bytes: {data.hex()}")
    if xor_checksum(data[:-1]) != data[-1]:
        raise ProtocolError(f"checksum mismatch: {data.hex()}")
    return Frame(pattern=data[4:7], cmd=data[7:9], payload=data[9:-1])


def build_frame(pattern: bytes, cmd: bytes, payload: bytes) -> bytes:
    body = HEADER + (10 + len(payload)).to_bytes(2, "little") + pattern + cmd + payload
    return body + bytes([xor_checksum(body)])


# --- fragments ----------------------------------------------------------------


@dataclass(frozen=True)
class Fragment:
    index: int
    total: int
    data: bytes


def parse_fragment(payload: bytes) -> Fragment:
    if not payload:
        raise ProtocolError("empty fragment")
    return Fragment(index=payload[0] >> 4, total=payload[0] & 0x0F, data=payload[1:])


class FragmentError(ProtocolError):
    """A fragment that does not continue its message; ``notifications`` are the ones collected."""

    def __init__(self, message: str, notifications: list[bytes]) -> None:
        super().__init__(message)
        self.notifications = notifications


class Reassembler:
    """Collect the fragments of a message, per (pattern, cmd), until it is complete.

    The notifications the fragments arrived in are kept alongside, so that the
    record of a joined message can carry the exact bytes it was made from.
    """

    def __init__(self) -> None:
        self._pending: dict[bytes, list[tuple[bytes, bytes]]] = {}  # (data, notification)

    def is_fragment(self, frame: Frame, notification_length: int, mtu: int) -> bool:
        """A notification that fills the MTU starts a fragmented message; later
        notifications with the same pattern and cmd continue it."""
        return notification_length == mtu or frame.key in self._pending

    def add(self, frame: Frame, notification: bytes) -> tuple[bytes, list[bytes]] | None:
        """Add one fragment (``notification`` is the whole notification it came in).

        Returns the full payload and the notifications it was joined from once all
        fragments are in, else ``None``. A fragment that cannot continue the message
        raises :class:`FragmentError` and forgets the message.
        """
        collected = self._pending.setdefault(frame.key, [])
        try:
            fragment = parse_fragment(frame.payload)
            if fragment.index != len(collected) + 1:
                raise ProtocolError(f"fragment {fragment.index}/{fragment.total} out of order")
        except ProtocolError as exc:
            del self._pending[frame.key]
            raise FragmentError(str(exc), [n for _, n in collected] + [notification]) from None
        collected.append((fragment.data, notification))
        if fragment.index != fragment.total:
            return None
        del self._pending[frame.key]
        return b"".join(d for d, _ in collected), [n for _, n in collected]

    def pending(self) -> dict[bytes, list[bytes]]:
        """The notifications of messages that are still incomplete, per (pattern, cmd)."""
        return {key: [n for _, n in collected] for key, collected in self._pending.items()}


# --- parameters (TLV) -------------------------------------------------------------


@dataclass(frozen=True)
class Parameter:
    key: int
    type: int | None
    value: bytes

    @property
    def raw(self) -> bytes:
        """Type byte (if any) followed by the value: the bytes after the length."""
        return (bytes([self.type]) if self.type is not None else b"") + self.value


def parse_parameters(payload: bytes) -> tuple[bool, dict[int, Parameter]]:
    """Split a payload into parameters; the flag says whether the ``00`` prefix was present."""
    prefix = payload[:1] == b"\x00"
    position = 1 if prefix else 0
    parameters: dict[int, Parameter] = {}
    while position < len(payload):
        if position + 2 > len(payload):
            raise ProtocolError(f"truncated parameter at {position}: {payload.hex()}")
        key, length = payload[position], payload[position + 1]
        position += 2
        if position + length > len(payload):
            raise ProtocolError(f"parameter {key:02x} runs past the end: {payload.hex()}")
        body = payload[position : position + length]
        position += length
        if length > 1:
            parameters[key] = Parameter(key, body[0], body[1:])
        else:
            parameters[key] = Parameter(key, None, body)
    return prefix, parameters


def build_parameters(parameters: Iterable[Parameter], prefix: bool = False) -> bytes:
    out = bytearray(b"\x00" if prefix else b"")
    for parameter in parameters:
        raw = parameter.raw
        out += bytes([parameter.key, len(raw)]) + raw
    return bytes(out)


# --- keys and ciphers -------------------------------------------------------------


@functools.cache
def _private_key(scalar: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())


def public_key_xy(private_key: bytes) -> bytes:
    """The uncompressed P-256 public point (x || y, 64 bytes) of a private scalar."""
    public = _private_key(private_key).public_key()
    return public.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:]


def shared_secret(private_key: bytes, peer_public_xy: bytes) -> bytes:
    """ECDH on P-256 between our private scalar and the peer's x || y point."""
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + peer_public_xy)
    return _private_key(private_key).exchange(ec.ECDH(), peer)


class CbcCipher:
    """AES-128-CBC with PKCS7, key = secret[:16], IV = secret[16:32] (Solix devices)."""

    def __init__(self, secret: bytes) -> None:
        self._key = secret[:16]
        self._iv = secret[16:32]

    def encrypt(self, plain: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plain) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, payload: bytes) -> tuple[bytes, bool]:
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
        padded = decryptor.update(payload) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        try:
            return unpadder.update(padded) + unpadder.finalize(), True
        except ValueError as exc:
            raise ProtocolError(f"bad padding after decrypt: {exc}") from exc


class GcmCipher:
    """AES-128-GCM with a fixed nonce and AAD, key = secret[:16], nonce = secret[16:28]
    (Anker Prime devices; before the key exchange a static key and nonce are used)."""

    def __init__(self, key: bytes, nonce: bytes, aad: bytes) -> None:
        self._key = key
        self._nonce = nonce
        self._aad = aad

    @classmethod
    def from_secret(cls, secret: bytes, aad: bytes) -> "GcmCipher":
        return cls(secret[:16], secret[16:28], aad)

    def encrypt(self, plain: bytes) -> bytes:
        return AESGCM(self._key).encrypt(self._nonce, plain, self._aad)

    def decrypt(self, payload: bytes) -> tuple[bytes, bool]:
        """The plaintext and whether the tag verified; an unverified plaintext is still
        returned because some devices have been seen to send a tag that does not check."""
        if len(payload) < 16:
            raise ProtocolError(f"GCM payload too short: {payload.hex()}")
        try:
            return AESGCM(self._key).decrypt(self._nonce, payload, self._aad), True
        except InvalidTag:
            decryptor = Cipher(algorithms.AES(self._key), modes.GCM(self._nonce)).decryptor()
            return decryptor.update(payload[:-16]), False
