import json
from datetime import UTC, datetime

from frostlog import records


def test_ambient_round_trip() -> None:
    record = records.ambient("dht20", 31.2, 58.4)
    line = records.to_json(record)
    parsed = records.from_json(line)
    assert parsed == record
    assert json.loads(line)["type"] == "ambient"


def test_cooler_payload_is_kept_verbatim() -> None:
    payload = {"pattern": "03010f", "cmd": "c405", "frames": ["ff09"], "plain": "00a1"}
    record = records.cooler("everfrost", "AA:BB", payload)
    parsed = records.from_json(records.to_json(record))
    assert isinstance(parsed, records.Cooler) and parsed.payload == payload
    assert parsed.address == "AA:BB"


def test_event_keeps_extra_fields() -> None:
    record = records.event("ble_connected", address="AA:BB")
    parsed = records.from_json(records.to_json(record))
    assert isinstance(parsed, records.Event)
    assert parsed.kind == "ble_connected"
    assert json.loads(records.to_json(parsed))["address"] == "AA:BB"


def test_common_fields_present() -> None:
    record = records.event("x")
    assert record.ts.tzinfo is UTC or record.ts.utcoffset() == datetime.now(UTC).utcoffset()
    assert record.uptime > 0
    assert record.boot_id
    assert isinstance(record.clock_synced, bool)
    assert json.loads(records.to_json(record))["clock_synced"] == record.clock_synced


def test_records_without_clock_synced_still_parse() -> None:
    line = '{"ts":"2026-09-06T11:02:11Z","uptime":1.5,"boot_id":"b","type":"event","kind":"x"}'
    assert records.from_json(line).clock_synced is None


def test_stream_dirs() -> None:
    assert records.stream_dir(records.event("x")) == "events"
    assert records.stream_dir(records.ambient("s", 1.0, 2.0)) == "ambient"
    assert records.stream_dir(records.cooler("m", "AA:BB", {})) == "cooler"
