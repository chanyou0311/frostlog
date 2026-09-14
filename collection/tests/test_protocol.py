"""Round trips against captures published by flip-dots/SolixBLE (MIT)."""

from itertools import pairwise

import pytest

from frostlog.cooler.everfrost import handshake as hs
from frostlog.cooler.everfrost import protocol as p

# --- vectors from SolixBLE/const.py and tests/const.py --------------------------------

SOLIX_UUID = "b2dc0b17-b75d-4abf-ba6e-ec7c997c23e7"
SOLIX_SENT = [
    # (what the library sent, what the C1000 answered)
    (
        "ff0936000300010001a10442ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537b9",
        "ff090e00030001080100a1010152",
    ),
    (
        "ff093d000300010003a10442ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537a30120a40200f064",
        "ff091b00030001080300a10102a202fd00a30144a40101a50102ff",
    ),
    (
        "ff0936000300010029a10442ad8c69a22462326463306231372d623735642d346162662d626136652d65633763393937633233653791",
        "ff093800030001082900a10103a2054553503332a307302e302e302e33a41041504339464530453237333030323735a506f49d8a104e0c9a",
    ),
    (
        "ff0940000300010005a10442ad8c69a22462326463306231372d623735642d346162662d626136652d656337633939376332336537a30120a40200f0a50140fb",
        "ff090b00030001080500f2",
    ),
    (
        "ff094c000300010021a140060ea168f232aedb37fb2d120c49180329ac72ab5ec3eb8fd30a2f252dc5e151dabccd9b1dc1e288704ca760a0d8c918e5c94823a1f609a4bf07fb4c33ee219085",
        "ff094d00030001082100a140b2ade5cac4f4a0c1307e44a0e9c5363cb21e4c8485ee324c23be949fa5d5929a75e57da3207c948a0c366ca9ea1ab2cb8e57d2d046a6ebefe5d96adb5d4cb35039",
    ),
]
SOLIX_STAGE5_SENT = "ff095a00030001402222c97d5c5bf02e0b43c62c864817cd38b9fd152113728513cc88bc4a1b4de3062473fcd5819618c4b926694d2732c337095a18974243127aa5e266f76f9ac7de06ba357763abe88aaef98f8c7a5e48a324"

PRIME_UUID = "79ebed35-dc9c-4904-b40c-72c4e863aa10"
PRIME_SENT = [
    (
        "ff09200003000140010a82d0ab535303e3aa9f0c2f9c868465bc8476f556fb7d",
        "ff091e000300014801ab273ed3e27270c3f4d676ac7d69a00572793732a6",
    ),
    (
        "ff09270003000140030a82d0ab53538ab3de100ac9bb87a0b8e36c1dd8167a9c25a9839d9a14d5",
        "ff092b000300014803ab273ed0443800b35db54c6d4a6ec3d48171a04ea7ebce8bf749e5e48c5d991a5e67",
    ),
    (
        "ff09200003000140290a82d0ab535303e3aa9f0c2f9c868465bc8476f556fb55",
        "ff0958000300014829ab273ed144326ada9fc66fa02508c5ddf549ade014d1eeb252fea1057c15b00985ab8a724fa3830e8e5b27acbaa1224fd2172c0439d27aaf9e62a66bda5c41c424f23c5c8d7df8d3b89422ddff2266",
    ),
    (
        "ff092d0003000140050a82d0ab53538ab3de100ae04aca6791257881a90164eac7460450e0c82f2c03de4f9604",
        "ff091b000300014805abab709a595a803dd04246b78a927453cf65",
    ),
    (
        "ff095c0003000140210ac6ea31e4300bb2877d6ddeb628b0d7be8d768333f00ceab5454d20fbd97e091457b1f3b6efb6511eb9e98ac2b2c46eee211ae359ad246e1ae9886b4a29e41eddd5a5064d8b9ffdbfb43eb6b8e307fcde9de7",
        "ff095d000300014821ab277fc01de436d341de628c79c1384d0aea25ce030622fa3ca0808ce5d1b7365ec1b1753a11ab78fba3ca07dda95cd57c93d1267b1222bef9908f7633a758ab924eba63ee01e715be5b9c3b082e6d81c2204241",
    ),
]
PRIME_STAGE6 = (
    "ff094000030001402257ec76586f3500c8f858e0ba047f237f4e2ed8c50d2f39ba3587e4010275bea22242936f08784271e19d67a6275ff6bb50577acec0a068",
    "ff091b000300014822f60b45600839b2c171b33dc5790ed64ae32d",
)
PRIME_STAGE7 = (
    "ff094600030001402757ec76586f3501e8cf6185d8c4035707377af9af3a2e40b02b86e7531974f1c22440de6e43705566b77cf940280d70e86b1fa915ab5a360040237091b9",
    "ff091b000300014827f60b45600839b2c171b33dc5790ed64ae328",
)
PRIME_SUBSCRIBE = [
    "ff09230003000f420057e9b8dfdeb3da799151684e584bb99eaaccfac9baf7cbcfa6e4",
    "ff09530003000f420a57e9b883d958e48e5b7de48d980206577e2dafbb3d604dea3686f3011969f0db2311906d142b5730ee2bfb11e3fbbe7485aac887798a31066997edf60c074ea9e1d5351970e9fa5a2960",
]


def h(text: str) -> bytes:
    return bytes.fromhex(text)


# --- frames and parameters ----------------------------------------------------------


def test_frame_round_trip() -> None:
    for sent, received in SOLIX_SENT:
        for raw in (h(sent), h(received)):
            frame = p.parse_frame(raw)
            assert p.build_frame(frame.pattern, frame.cmd, frame.payload) == raw
    frame = p.parse_frame(h(SOLIX_SENT[0][0]))
    assert frame.pattern == p.PATTERN_NEGOTIATION
    assert frame.cmd == h("0001")


def test_frame_errors() -> None:
    raw = bytearray(h(SOLIX_SENT[0][1]))
    raw[-1] ^= 1
    with pytest.raises(p.ProtocolError, match="checksum"):
        p.parse_frame(bytes(raw))
    with pytest.raises(p.ProtocolError, match="length"):
        p.parse_frame(h(SOLIX_SENT[0][1])[:-2])
    with pytest.raises(p.ProtocolError, match="not a frame"):
        p.parse_frame(b"\x00\x01")


def test_parameters_parse_device_info() -> None:
    frame = p.parse_frame(h(SOLIX_SENT[2][1]))
    prefix, parameters = p.parse_parameters(frame.payload)
    assert prefix is True
    assert parameters[0xA1].raw == b"\x03"
    assert parameters[0xA2].raw == b"ESP32"
    assert parameters[0xA3].raw == b"0.0.0.3"
    assert parameters[0xA4].raw == b"APC9FE0E27300275"
    assert len(parameters[0xA5].raw) == 6
    # Type byte is the first byte of anything longer than one byte.
    assert parameters[0xA2].type == ord("E") and parameters[0xA2].value == b"SP32"
    assert p.build_parameters(parameters.values(), prefix=True) == frame.payload


def test_parameters_truncated() -> None:
    with pytest.raises(p.ProtocolError):
        p.parse_parameters(h("a105aabb"))


def test_fragment_reassembly() -> None:
    reassembler = p.Reassembler()
    frames = [
        p.Frame(h("03010f"), h("c405"), h("13") + b"abc"),
        p.Frame(h("03010f"), h("c405"), h("23") + b"def"),
        p.Frame(h("03010f"), h("c405"), h("33") + b"g"),
    ]
    raw = [b"n1", b"n2", b"n3"]
    assert reassembler.is_fragment(frames[0], notification_length=253, mtu=253)
    assert reassembler.add(frames[0], raw[0]) is None
    assert reassembler.is_fragment(frames[1], notification_length=100, mtu=253)
    assert reassembler.add(frames[1], raw[1]) is None
    assert reassembler.pending() == {frames[0].key: raw[:2]}
    assert reassembler.add(frames[2], raw[2]) == (b"abcdefg", raw)
    assert not reassembler.is_fragment(frames[1], notification_length=100, mtu=253)
    assert reassembler.pending() == {}

    reassembler.add(frames[0], raw[0])
    with pytest.raises(p.FragmentError, match="out of order") as info:
        reassembler.add(frames[2], raw[2])
    assert info.value.notifications == [raw[0], raw[2]]
    assert reassembler.pending() == {}

    # A frame that is no fragment at all also ends the message, keeping what was collected.
    reassembler.add(frames[0], raw[0])
    with pytest.raises(p.FragmentError, match="empty") as info:
        reassembler.add(p.Frame(h("03010f"), h("c405"), b""), b"n0")
    assert info.value.notifications == [raw[0], b"n0"]
    assert reassembler.pending() == {}


# --- keys -------------------------------------------------------------------------


def test_client_public_keys_match_the_fixed_private_keys() -> None:
    solix_stage4 = p.parse_frame(h(SOLIX_SENT[4][0]))
    assert (
        p.public_key_xy(hs.SOLIX_PRIVATE_KEY)
        == p.parse_parameters(solix_stage4.payload)[1][0xA1].raw
    )
    prime_static = hs.GcmCipher(hs.PRIME_NEGOTIATION_KEY, hs.PRIME_NEGOTIATION_NONCE, hs.PRIME_AAD)
    prime_stage4 = p.parse_frame(h(PRIME_SENT[4][0]))
    plain, verified = prime_static.decrypt(prime_stage4.payload)
    assert verified
    assert p.public_key_xy(hs.PRIME_PRIVATE_KEY) == p.parse_parameters(plain)[1][0xA1].raw


# --- Solix handshake ---------------------------------------------------------------


def _timestamp_in(payload: bytes) -> int:
    return int.from_bytes(p.parse_parameters(payload)[1][0xA1].raw, "little")


def test_solix_handshake_reproduces_capture() -> None:
    timestamp = _timestamp_in(p.parse_frame(h(SOLIX_SENT[0][0])).payload)
    handshake = hs.SolixHandshake(timestamp=lambda: timestamp, posix_tz="GMT0BST,M3.5.0/1,M10.5.0")
    assert handshake.start() == [h(SOLIX_SENT[0][0])]
    for (_, received), (next_sent, _) in pairwise(SOLIX_SENT):
        assert handshake.handle(p.parse_frame(h(received))) == [h(next_sent)]
        assert not handshake.done
    assert handshake.mtu == 253
    assert handshake.device == hs.DeviceInfo(
        chip="ESP32", firmware="0.0.0.3", serial="APC9FE0E27300275"
    )

    replies = handshake.handle(p.parse_frame(h(SOLIX_SENT[4][1])))
    assert handshake.done and handshake.secret is not None and handshake.cipher is not None
    assert len(replies) == 1
    reply = p.parse_frame(replies[0])
    assert reply.cmd == h("4022")
    plain, _ = handshake.cipher.decrypt(reply.payload)
    parameters = p.parse_parameters(plain)[1]
    assert parameters[0xA2].raw == SOLIX_UUID.encode()
    assert parameters[0xA4].raw == b"\x00\x00\x00\x00"

    # The library's own stage-5 reply decrypts with the secret we derived.
    captured = p.parse_frame(h(SOLIX_STAGE5_SENT))
    plain, _ = handshake.cipher.decrypt(captured.payload)
    parameters = p.parse_parameters(plain)[1]
    assert parameters[0xA2].raw == SOLIX_UUID.encode()
    assert parameters[0xA5].raw.startswith(b"GMT0BST")


def test_solix_stage5_without_public_key_fails_clearly() -> None:
    handshake = hs.SolixHandshake(timestamp=lambda: 0)
    frame = p.Frame(p.PATTERN_NEGOTIATION, h("0821"), h("00ab0102"))
    with pytest.raises(hs.HandshakeError, match="a1 missing"):
        handshake.handle(frame)


def test_handshake_ignores_other_patterns() -> None:
    handshake = hs.SolixHandshake(timestamp=lambda: 0)
    assert handshake.handle(p.Frame(h("03010f"), h("c405"), b"")) == []


# --- Prime handshake ---------------------------------------------------------------


def test_prime_handshake_reproduces_capture() -> None:
    static = hs.GcmCipher(hs.PRIME_NEGOTIATION_KEY, hs.PRIME_NEGOTIATION_NONCE, hs.PRIME_AAD)
    clock = {"now": 0}
    handshake = hs.PrimeHandshake(
        timestamp=lambda: clock["now"], posix_tz="GMT0BST,M3.5.0/1,M10.5.0"
    )

    def expect(sent_hex: str, produced: list[bytes]) -> None:
        assert produced == [h(sent_hex)]

    clock["now"] = _timestamp_in(static.decrypt(p.parse_frame(h(PRIME_SENT[0][0])).payload)[0])
    expect(PRIME_SENT[0][0], handshake.start())
    for (_, received), (next_sent, _) in pairwise(PRIME_SENT):
        plain, verified = static.decrypt(p.parse_frame(h(next_sent)).payload)
        assert verified
        if (
            0xA1 in p.parse_parameters(plain)[1]
            and len(p.parse_parameters(plain)[1][0xA1].raw) == 4
        ):
            clock["now"] = _timestamp_in(plain)
        expect(next_sent, handshake.handle(p.parse_frame(h(received))))
    assert handshake.mtu == 297  # the Prime charger's MTU, as reported at stage 2

    # Stage 5: derive the secret; from here on the device's messages verify with it.
    replies = handshake.handle(p.parse_frame(h(PRIME_SENT[4][1])))
    assert handshake.secret is not None and handshake.cipher is not None and not handshake.done
    assert p.parse_frame(replies[0]).cmd == h("4022")
    plain, verified = handshake.cipher.decrypt(p.parse_frame(h(PRIME_STAGE6[1])).payload)
    assert verified and len(plain) == 1
    plain, verified = handshake.cipher.decrypt(p.parse_frame(h(PRIME_STAGE6[0])).payload)
    assert verified and p.parse_parameters(plain)[1][0xA5].raw.startswith(b"GMT0BST")

    replies = handshake.handle(p.parse_frame(h(PRIME_STAGE6[1])))
    reply = p.parse_frame(replies[0])
    assert reply.cmd == h("4027")
    plain, _ = handshake.cipher.decrypt(reply.payload)
    assert p.parse_parameters(plain)[1][0xA2].raw == PRIME_UUID.encode()

    replies = handshake.handle(p.parse_frame(h(PRIME_STAGE7[1])))
    assert handshake.done
    assert [p.parse_frame(r).cmd.hex() for r in replies] == ["4200", "420a"]
    assert all(p.parse_frame(r).pattern == p.PATTERN_COMMAND for r in replies)
    for captured in PRIME_SUBSCRIBE:
        plain, verified = handshake.cipher.decrypt(p.parse_frame(h(captured)).payload)
        assert verified and p.parse_parameters(plain)[1][0xA1].raw == b"\x21"


def test_gcm_returns_unverified_plaintext_on_bad_tag() -> None:
    cipher = hs.GcmCipher(hs.PRIME_NEGOTIATION_KEY, hs.PRIME_NEGOTIATION_NONCE, hs.PRIME_AAD)
    payload = bytearray(cipher.encrypt(b"hello"))
    payload[-1] ^= 1
    plain, verified = cipher.decrypt(bytes(payload))
    assert plain == b"hello" and verified is False


def test_cbc_round_trip_and_bad_padding() -> None:
    cipher = p.CbcCipher(bytes(range(32)))
    assert cipher.decrypt(cipher.encrypt(b"x" * 20)) == (b"x" * 20, True)
    with pytest.raises(p.ProtocolError):
        cipher.decrypt(bytes(16))
