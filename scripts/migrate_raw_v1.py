#!/usr/bin/env python3
"""Rewrite the Pi's local record files into the shape of the raw v1 data contract.

One-off, run on the Pi with the collector stopped, and before the v1 uploader has
ever run on that directory: every record is rewritten, so every byte offset in
the files moves, and offsets are what the bucket names its chunks after. Once a
chunk has been uploaded, a migration would make the offsets in the bucket mean
different lines. The advisory upload cache is deleted here for the same reason.

    systemctl --user stop frostlog-cooler frostlog-upload.timer
    ~/frostlog/.venv/bin/python ~/frostlog/scripts/migrate_raw_v1.py ~/.local/state/frostlog

What changes: ``uptime`` becomes ``uptime_seconds``; a cooler record's nested
``payload`` object is flattened onto the record, keeping ``frames`` and the body
in the clear and dropping the encrypted payload (a copy of the frames) and
``plain_verified``; state reports get their decoded ``payload``; the Bluetooth
address comes from the ``ble_connected`` event the message followed; the
environment comes from the ambient reading nearest in time within 15 seconds,
after which the ambient stream is deleted, because from v1 on the environment is
measured with each message and lives on the message's record.

``ts_synced`` is null wherever the old record does not say: those records were
made before the collector knew whether the clock had been set. ``plain`` stays
absent unless the body really is in the clear: the old record kept the payload
as received, which is ciphertext for every message of an encrypted session.

Lines that a power cut left torn or filled with NUL bytes are dropped; the
uploader never shipped them either. Running the script again changes nothing:
a record that is already in the v1 shape is left alone.
"""

import argparse
import bisect
import json
import logging
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from frostlog import store
from frostlog.cooler.everfrost.decoder import CMD_STATE, decode_state_or_none
from frostlog.cooler.everfrost.protocol import PATTERN_NEGOTIATION
from frostlog.upload.cache import NAME as UPLOAD_CACHE

log = logging.getLogger("migrate")

#: How far a cabin reading may be from a message and still describe its moment.
ENVIRONMENT_WINDOW_SECONDS = 15.0

#: The device's replies while the Solix negotiation is still in the clear: up to
#: and including 0821 there is no session key, so their body was sent as it is.
CLEAR_PATTERN = PATTERN_NEGOTIATION.hex()
CLEAR_CMDS = frozenset({"0801", "0803", "0805", "0821", "0829"})


class MissingAddress(Exception):
    """No connection event says which cooler a message came from."""


def uptime_of(row: dict[str, Any]) -> float:
    return float(row.get("uptime_seconds", row.get("uptime", 0.0)))


def is_v1(row: dict[str, Any]) -> bool:
    return "uptime_seconds" in row


def read_rows(path: Path) -> Iterator[tuple[bytes, dict[str, Any] | None]]:
    """Every complete line of a record file with its parsed record, or ``None`` if unreadable."""
    with path.open("rb") as file:
        for raw in file:
            if not raw.endswith(b"\n"):
                continue  # torn by a power cut
            if not store.readable(raw):
                yield raw, None
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                yield raw, None
                continue
            yield raw, row if isinstance(row, dict) else None


def connection_index(root: Path) -> dict[str, list[tuple[float, str]]]:
    """Per boot, the ``ble_connected`` events in order: which cooler was talking when."""
    index: dict[str, list[tuple[float, str]]] = {}
    for path in sorted((root / "events").glob("*.jsonl")):
        for _, row in read_rows(path):
            if row is None or row.get("kind") != "ble_connected" or not row.get("address"):
                continue
            index.setdefault(str(row.get("boot_id")), []).append(
                (uptime_of(row), str(row["address"]))
            )
    for connections in index.values():
        connections.sort()
    return index


def environment_index(root: Path) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    """Per boot, the ambient readings in order."""
    index: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for path in sorted((root / "ambient").glob("*.jsonl")):
        for _, row in read_rows(path):
            if row is None or row.get("type") != "ambient":
                continue
            index.setdefault(str(row.get("boot_id")), []).append(
                (
                    uptime_of(row),
                    {
                        "sensor": row.get("sensor"),
                        "temperature_celsius": row.get("temperature_celsius", row.get("temp_c")),
                        "humidity_percent": row.get("humidity_percent", row.get("humidity_pct")),
                    },
                )
            )
    for readings in index.values():
        readings.sort(key=lambda reading: reading[0])
    return index


def address_at(
    index: dict[str, list[tuple[float, str]]], boot_id: str, uptime: float
) -> str | None:
    """The cooler that was connected when the message arrived."""
    connections = index.get(boot_id, [])
    position = bisect.bisect_right(connections, uptime, key=lambda connection: connection[0])
    return connections[position - 1][1] if position else None


def environment_at(
    index: dict[str, list[tuple[float, dict[str, Any]]]], boot_id: str, uptime: float
) -> dict[str, Any] | None:
    """The cabin reading nearest to the message, if one was taken close enough to it."""
    readings = index.get(boot_id, [])
    position = bisect.bisect_left(readings, uptime, key=lambda reading: reading[0])
    candidates = readings[max(0, position - 1) : position + 1]
    if not candidates:
        return None
    when, reading = min(candidates, key=lambda candidate: abs(candidate[0] - uptime))
    if abs(when - uptime) > ENVIRONMENT_WINDOW_SECONDS:
        return None
    return reading if all(value is not None for value in reading.values()) else None


def common(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": row.get("ts"),
        "uptime_seconds": uptime_of(row),
        "boot_id": row.get("boot_id"),
        # Older records do not say whether the clock had been set; that stays unknown.
        "ts_synced": row.get("ts_synced", row.get("clock_synced")),
    }


def migrate_cooler(
    row: dict[str, Any],
    connections: dict[str, list[tuple[float, str]]],
    environments: dict[str, list[tuple[float, dict[str, Any]]]],
    fallback_address: str | None,
) -> dict[str, Any]:
    old = row.get("payload") or {}
    boot_id, uptime = str(row.get("boot_id")), uptime_of(row)
    address = address_at(connections, boot_id, uptime) or fallback_address
    if address is None:
        raise MissingAddress(f"{boot_id} at {uptime}s: no ble_connected event and no --address")
    out = common(row) | {
        "type": "cooler",
        "model": row.get("model"),
        "address": address,
    }
    for field in ("pattern", "cmd"):
        if old.get(field):
            out[field] = old[field]
    out["frames"] = old.get("frames") or []
    plain = _plain(old)
    if plain is not None:
        out["plain"] = plain
        if old.get("cmd") == CMD_STATE:
            payload = _decoded(plain)
            if payload is not None:
                out["payload"] = payload
    environment = environment_at(environments, boot_id, uptime)
    if environment is not None:
        out["environment"] = environment
    if old.get("error"):
        out["error"] = old["error"]
    return out


def _plain(old: dict[str, Any]) -> str | None:
    """The body in the clear: as sent before the session was encrypted, else decrypted.

    The old record kept ``payload`` as received. That is the body itself only
    while the handshake is still in the clear; once the session key exists it is
    ciphertext, and a message whose decryption failed has no body in the clear at
    all (the contract has ``plain`` only when decryption verified).
    """
    if "plain" in old:
        return old["plain"] if old.get("plain_verified", True) else None
    return old.get("payload") if _sent_in_the_clear(old) else None


def _sent_in_the_clear(old: dict[str, Any]) -> bool:
    return old.get("pattern") == CLEAR_PATTERN and old.get("cmd") in CLEAR_CMDS


def _decoded(plain: str) -> dict[str, Any] | None:
    payload = decode_state_or_none(bytes.fromhex(plain))
    return None if payload is None else payload.model_dump(exclude_none=True)


def migrate_event(row: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: value
        for key, value in row.items()
        if key not in {"ts", "uptime", "uptime_seconds", "boot_id", "clock_synced", "ts_synced"}
    }
    return common(row) | fields


def rewrite(
    path: Path, convert: Callable[[dict[str, Any]], dict[str, Any]], counts: Counter[str]
) -> None:
    """Convert every record of a file, then put the result in its place."""
    lines: list[bytes] = []
    for raw, row in read_rows(path):
        if row is None:
            counts["dropped"] += 1
            continue
        if is_v1(row):
            counts["already v1"] += 1
            lines.append(raw)
            continue
        lines.append(json.dumps(convert(row)).encode("utf-8") + b"\n")
        counts["migrated"] += 1
    temporary = path.with_suffix(".migrating")
    temporary.write_bytes(b"".join(lines))
    os.replace(temporary, path)


def migrate(root: Path, fallback_address: str | None) -> Counter[str]:
    connections = connection_index(root)
    environments = environment_index(root)
    counts: Counter[str] = Counter()
    for path in sorted((root / "cooler").glob("*.jsonl")):
        log.info("%s", path)
        rewrite(
            path,
            lambda row: migrate_cooler(row, connections, environments, fallback_address),
            counts,
        )
    for path in sorted((root / "events").glob("*.jsonl")):
        log.info("%s", path)
        rewrite(path, migrate_event, counts)
    ambient = root / "ambient"
    if ambient.is_dir():
        shutil.rmtree(ambient)
        log.info("%s: removed (the environment now travels with each message)", ambient)
        counts["ambient files removed"] += 1
    cache = root / UPLOAD_CACHE
    if cache.is_file():
        cache.unlink()
        # Its offsets point into the files as they were before this rewrite.
        log.info("%s: removed (the offsets it remembers have all moved)", cache)
        counts["upload cache removed"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Root of the record files.")
    parser.add_argument(
        "--address",
        default=os.environ.get("FROSTLOG_COOLER_ADDRESS"),
        help="Bluetooth address for messages no connection event covers.",
    )
    arguments = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    if not arguments.directory.is_dir():
        parser.error(f"{arguments.directory} is not a directory")
    try:
        counts = migrate(arguments.directory, arguments.address)
    except MissingAddress as exc:
        log.error("%s", exc)
        return 1
    summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    log.info("done: %s", summary or "nothing to migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
