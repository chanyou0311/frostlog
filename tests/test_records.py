import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from frostlog import clock, records


def test_cooler_record_keeps_the_bytes_and_drops_what_is_absent() -> None:
    record = records.cooler(
        "everfrost",
        "AA:BB",
        ["ff09"],
        pattern="03010f",
        cmd="4402",
        plain="00a10105",
    )
    line = records.to_json(record)
    parsed = records.from_json(line)
    assert isinstance(parsed, records.Cooler)
    assert parsed.frames == ["ff09"] and parsed.plain == "00a10105"
    # A field with nothing in it is left out of the line, not written as null.
    written = json.loads(line)
    assert "payload" not in written and "environment" not in written and "error" not in written
    assert written["type"] == "cooler" and written["address"] == "AA:BB"


def test_cooler_record_with_everything_round_trips() -> None:
    payload = records.CoolerPayload(
        setpoint_celsius=-20,
        interior_temperature_celsius=-17,
        display_unit="C",
        input_watts=0,
        usb_a_output_watts=0,
        usb_c_output_watts=0,
        charge_watts=0,
        discharge_watts=43,
        battery_state="discharging",
        state_of_charge_percent=80,
        protection_level="M",
        brightness="low",
        serial_number="ANSDHW40F15400347",
        battery_serial_number="AZV17D20F14200480",
    )
    environment = records.Environment(
        sensor="dht20", temperature_celsius=25.21, humidity_percent=58.99
    )
    record = records.cooler(
        "everfrost", "AA:BB", ["ff09"], payload=payload, environment=environment
    )
    parsed = records.from_json(records.to_json(record))
    assert parsed == record
    written = json.loads(records.to_json(record))
    assert written["payload"]["battery_state"] == "discharging"
    assert written["environment"]["temperature_celsius"] == 25.21


def test_unknown_decoded_values_are_refused() -> None:
    with pytest.raises(ValueError):
        records.CoolerPayload(
            setpoint_celsius=-20,
            interior_temperature_celsius=-17,
            display_unit="K",  # ty: ignore[invalid-argument-type]
            input_watts=0,
            usb_a_output_watts=0,
            usb_c_output_watts=0,
            charge_watts=0,
            discharge_watts=0,
            battery_state="idle",
            state_of_charge_percent=80,
            protection_level="M",
            brightness="low",
            serial_number="x",
        )


def test_event_keeps_extra_fields() -> None:
    record = records.event("upload_done", uploaded_chunk_count=2, uploaded_line_count=311)
    parsed = records.from_json(records.to_json(record))
    assert isinstance(parsed, records.Event)
    assert parsed.kind == "upload_done"
    assert json.loads(records.to_json(parsed))["uploaded_line_count"] == 311


def test_common_fields_present() -> None:
    record = records.event("x")
    assert record.ts.tzinfo is UTC or record.ts.utcoffset() == datetime.now(UTC).utcoffset()
    assert record.uptime_seconds > 0
    assert record.boot_id
    assert isinstance(record.ts_synced, bool)
    assert json.loads(records.to_json(record))["ts_synced"] == record.ts_synced


def test_ts_synced_says_whether_the_clock_was_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wherever the file systemd-timesyncd creates is absent - a machine that is not the
    # Pi included - the answer is False, never a hopeful True.
    monkeypatch.setattr(clock, "_SYNCED_PATH", Path("/nonexistent/synchronized"))
    assert clock.synced() is False
    assert records.stamp()["ts_synced"] is False


def test_migrated_records_may_leave_ts_synced_unknown() -> None:
    line = (
        '{"ts":"2026-09-06T11:02:11Z","uptime_seconds":1.5,"boot_id":"b",'
        '"ts_synced":null,"type":"event","kind":"x"}'
    )
    assert records.from_json(line).ts_synced is None


def test_stream_dirs() -> None:
    assert records.stream_dir(records.event("x")) == "events"
    assert records.stream_dir(records.ambient("dht20", 1.0, 2.0)) == "ambient"
    assert records.stream_dir(records.cooler("everfrost", "AA:BB", [])) == "cooler"
