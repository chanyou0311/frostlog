"""The sample chunks in ``contracts/samples`` are records, and they are up to date.

``datacontract test --server samples`` checks them against the contract in CI; this
checks them against the code, so that a change to the record shape cannot leave the
samples behind.
"""

import importlib.util
import sys
from pathlib import Path

from frostlog import records

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "contracts" / "samples" / "raw"

_SPEC = importlib.util.spec_from_file_location(
    "build_samples", ROOT / "scripts" / "build_samples.py"
)
assert _SPEC and _SPEC.loader
build_samples = importlib.util.module_from_spec(_SPEC)
sys.modules["build_samples"] = build_samples
_SPEC.loader.exec_module(build_samples)


def _lines(stream: str) -> list[str]:
    path = SAMPLES / stream / "2026-09-06.json"
    return path.read_text(encoding="utf-8").splitlines()


def test_every_sample_line_is_a_record() -> None:
    for stream, expected in (("cooler", records.Cooler), ("events", records.Event)):
        lines = _lines(stream)
        assert lines
        for line in lines:
            assert isinstance(records.from_json(line), expected)


def test_the_samples_show_what_the_contract_describes() -> None:
    rows = [records.from_json(line) for line in _lines("cooler")]
    messages = [row for row in rows if isinstance(row, records.Cooler)]
    states = {row.payload.battery_state for row in messages if row.payload}
    assert states == {"idle", "charging", "discharging", "full", "absent"}
    assert any(row.payload and row.payload.display_unit == "F" for row in messages)
    assert any(row.error for row in messages)
    assert all(row.environment for row in messages)
    kinds = {records.from_json(line).model_dump()["kind"] for line in _lines("events")}
    assert {"ble_connected", "ble_silent", "upload_done", "environment_read_failed"} <= kinds


def test_the_committed_samples_are_what_the_builder_writes() -> None:
    written = {
        "cooler": build_samples.cooler_rows(),
        "events": build_samples.event_rows(),
    }
    for stream, rows in written.items():
        expected = "".join(records.line(row) for row in rows)
        path = SAMPLES / stream / "2026-09-06.json"
        assert path.read_text(encoding="utf-8") == expected, (
            "the samples are stale: run scripts/build_samples.py and commit the result"
        )
