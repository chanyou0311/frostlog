"""The one-off migration of the Pi's files, against a fixture in the old shape."""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import captured
import pytest

from frostlog import records

_SPEC = importlib.util.spec_from_file_location(
    "migrate_raw_v1", Path(__file__).resolve().parent.parent / "scripts" / "migrate_raw_v1.py"
)
assert _SPEC and _SPEC.loader
migrate_raw_v1 = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate_raw_v1"] = migrate_raw_v1
_SPEC.loader.exec_module(migrate_raw_v1)

BOOT = captured.BOOT_ID
OTHER_BOOT = "0f0c4c2c-1f1e-4d0c-8a4c-2a2d3f4e5a6b"


def _old_cooler(uptime: float, payload: dict[str, Any], boot: str = BOOT) -> str:
    return json.dumps(
        {
            "ts": "2026-09-06T11:02:19.909801Z",
            "uptime": uptime,
            "boot_id": boot,
            "type": "cooler",
            "model": "everfrost",
            "payload": payload,
        }
    )


def _old_ambient(uptime: float, temperature: float, boot: str = BOOT) -> str:
    return json.dumps(
        {
            "ts": "2026-09-06T11:02:19.000000Z",
            "uptime": uptime,
            "boot_id": boot,
            "type": "ambient",
            "sensor": "dht20",
            "temp_c": temperature,
            "humidity_pct": 58.99,
        }
    )


def _old_event(uptime: float, kind: str, boot: str = BOOT, **fields: Any) -> str:
    return json.dumps(
        {
            "ts": "2026-09-06T11:02:10.577919Z",
            "uptime": uptime,
            "boot_id": boot,
            "type": "event",
            "kind": kind,
            **fields,
        }
    )


@pytest.fixture
def old_records(tmp_path: Path) -> Path:
    state = captured.DISCHARGING
    (tmp_path / "cooler").mkdir()
    (tmp_path / "ambient").mkdir()
    (tmp_path / "events").mkdir()
    (tmp_path / "cooler/2026-09-06.jsonl").write_bytes(
        "\n".join(
            [
                # A handshake reply, sent before the session was encrypted.
                _old_cooler(
                    100.0,
                    {
                        "pattern": "030001",
                        "cmd": "0829",
                        "frames": [captured.HANDSHAKE_FRAME],
                        "payload": captured.HANDSHAKE_PLAIN,
                    },
                ),
                # A state report, decrypted and verified.
                _old_cooler(
                    110.0,
                    {
                        "pattern": "03010f",
                        "cmd": "4402",
                        "frames": state.frames,
                        "payload": "f921d642",
                        "plain": state.plain,
                        "plain_verified": True,
                    },
                ),
                # A plaintext that did not verify is not a plaintext.
                _old_cooler(
                    120.0,
                    {
                        "pattern": "03010f",
                        "cmd": "4402",
                        "frames": state.frames,
                        "payload": "f921d642",
                        "plain": state.plain,
                        "plain_verified": False,
                    },
                ),
                # A message that never arrived whole.
                _old_cooler(
                    130.0,
                    {
                        "pattern": "03010f",
                        "cmd": "c405",
                        "frames": ["ff09"],
                        "error": "incomplete",
                    },
                ),
                # Another boot, whose connection went to a different address.
                _old_cooler(
                    10.0,
                    {"pattern": "030001", "cmd": "0801", "frames": ["ff09"], "payload": "00"},
                    boot=OTHER_BOOT,
                ),
            ]
        ).encode("utf-8")
        + b"\n\x00\x00\x00\n"  # a line the power cut filled with NUL bytes
    )
    (tmp_path / "ambient/2026-09-06.jsonl").write_text(
        "\n".join(
            [
                _old_ambient(100.5, 25.21),
                _old_ambient(112.0, 25.34),  # nearest to the state report at 110 s
                _old_ambient(180.0, 26.0),  # too far from anything
                _old_ambient(9.0, 30.0, boot=OTHER_BOOT),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "events/2026-09-06.jsonl").write_text(
        "\n".join(
            [
                _old_event(99.0, "ble_connected", address=captured.ADDRESS, name=captured.NAME),
                _old_event(9.5, "ble_connected", boot=OTHER_BOOT, address="AA:BB:CC:DD:EE:FF"),
                _old_event(150.0, "ambient_read_failed", sensor="dht20", error="I2C error"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _rows(path: Path) -> list[records.Record]:
    return [records.from_json(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_migrated_records_match_the_contract(old_records: Path) -> None:
    counts = migrate_raw_v1.migrate(old_records, None)
    assert counts["dropped"] == 1  # the NUL line
    rows = _rows(old_records / "cooler/2026-09-06.jsonl")
    assert len(rows) == 5
    assert all(isinstance(row, records.Cooler) for row in rows)
    assert all(row.ts_synced is None for row in rows)
    assert all(row.uptime_seconds > 0 for row in rows)


def test_the_address_comes_from_the_connection_the_message_followed(old_records: Path) -> None:
    migrate_raw_v1.migrate(old_records, None)
    rows = _rows(old_records / "cooler/2026-09-06.jsonl")
    addresses = [row.address for row in rows if isinstance(row, records.Cooler)]
    assert addresses[:4] == [captured.ADDRESS] * 4
    assert addresses[4] == "AA:BB:CC:DD:EE:FF"  # the other boot's connection


def test_a_state_report_is_decoded_and_an_unverified_plaintext_is_dropped(
    old_records: Path,
) -> None:
    migrate_raw_v1.migrate(old_records, None)
    handshake, decoded, unverified, incomplete, _ = _rows(old_records / "cooler/2026-09-06.jsonl")
    assert isinstance(decoded, records.Cooler) and decoded.payload is not None
    assert decoded.payload.state_of_charge_percent == 80
    assert isinstance(handshake, records.Cooler)
    assert handshake.plain == captured.HANDSHAKE_PLAIN  # sent in the clear
    assert handshake.payload is None
    assert isinstance(unverified, records.Cooler)
    assert unverified.plain is None and unverified.payload is None
    assert isinstance(incomplete, records.Cooler) and incomplete.error == "incomplete"


def test_the_body_of_an_encrypted_message_is_never_taken_for_a_plaintext(
    tmp_path: Path,
) -> None:
    """The old record kept the payload as received: ciphertext once the session has a key."""
    (tmp_path / "cooler").mkdir()
    (tmp_path / "cooler/2026-09-06.jsonl").write_text(
        "\n".join(
            [
                # In the clear: the negotiation is not encrypted up to this reply.
                _old_cooler(
                    10.0,
                    {
                        "pattern": "030001",
                        "cmd": "0829",
                        "frames": [captured.HANDSHAKE_FRAME],
                        "payload": captured.HANDSHAKE_PLAIN,
                    },
                ),
                # Encrypted, and its decryption failed: there is no body in the clear.
                _old_cooler(
                    20.0,
                    {
                        "pattern": "03010f",
                        "cmd": "4402",
                        "frames": captured.DISCHARGING.frames,
                        "payload": "f921d642",
                    },
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    migrate_raw_v1.migrate(tmp_path, captured.ADDRESS)
    handshake, encrypted = _rows(tmp_path / "cooler/2026-09-06.jsonl")
    assert isinstance(handshake, records.Cooler) and handshake.plain == captured.HANDSHAKE_PLAIN
    assert isinstance(encrypted, records.Cooler)
    assert encrypted.plain is None and encrypted.payload is None
    assert encrypted.frames == captured.DISCHARGING.frames  # the bytes are kept


def test_the_upload_cache_goes_with_the_offsets_it_remembers(old_records: Path) -> None:
    cache = old_records / ".upload-cache.json"
    cache.write_text(
        '{"location": "x", "ends": {"cooler/2026-09-06.jsonl": 512}}', encoding="utf-8"
    )
    counts = migrate_raw_v1.migrate(old_records, None)
    assert not cache.exists()  # every byte offset in the files just moved
    assert counts["upload cache removed"] == 1


def test_the_environment_comes_from_the_reading_of_the_same_moment(old_records: Path) -> None:
    migrate_raw_v1.migrate(old_records, None)
    rows = [row for row in _rows(old_records / "cooler/2026-09-06.jsonl")]
    handshake, decoded, _, incomplete, other = rows
    assert isinstance(handshake, records.Cooler) and handshake.environment is not None
    assert handshake.environment.temperature_celsius == 25.21
    assert isinstance(decoded, records.Cooler) and decoded.environment is not None
    assert decoded.environment.temperature_celsius == 25.34
    assert isinstance(incomplete, records.Cooler)
    assert incomplete.environment is None  # nothing was measured within 15 s
    assert isinstance(other, records.Cooler) and other.environment is not None
    assert other.environment.temperature_celsius == 30.0  # its own boot's reading


def test_the_ambient_stream_is_gone_afterwards(old_records: Path) -> None:
    migrate_raw_v1.migrate(old_records, None)
    assert not (old_records / "ambient").exists()


def test_events_keep_their_fields(old_records: Path) -> None:
    migrate_raw_v1.migrate(old_records, None)
    rows = _rows(old_records / "events/2026-09-06.jsonl")
    connected = rows[0]
    assert isinstance(connected, records.Event)
    assert connected.kind == "ble_connected"
    assert connected.model_dump()["address"] == captured.ADDRESS
    assert connected.uptime_seconds == 99.0 and connected.ts_synced is None


def test_running_it_again_changes_nothing(old_records: Path) -> None:
    migrate_raw_v1.migrate(old_records, None)
    before = (old_records / "cooler/2026-09-06.jsonl").read_bytes()
    counts = migrate_raw_v1.migrate(old_records, None)
    assert (old_records / "cooler/2026-09-06.jsonl").read_bytes() == before
    assert counts["migrated"] == 0 and counts["already v1"] == 8  # 5 cooler, 3 events


def test_a_message_no_connection_covers_needs_an_address_to_fall_back_on(tmp_path: Path) -> None:
    (tmp_path / "cooler").mkdir()
    (tmp_path / "cooler/2026-09-06.jsonl").write_text(
        _old_cooler(10.0, {"cmd": "0801", "frames": ["ff09"], "payload": "00"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(migrate_raw_v1.MissingAddress):
        migrate_raw_v1.migrate(tmp_path, None)
    migrate_raw_v1.migrate(tmp_path, captured.ADDRESS)
    row = _rows(tmp_path / "cooler/2026-09-06.jsonl")[0]
    assert isinstance(row, records.Cooler) and row.address == captured.ADDRESS
