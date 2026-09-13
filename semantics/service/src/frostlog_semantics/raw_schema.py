"""The BigQuery schema of the raw tables, derived from contracts/collection.odcs.yaml.

The raw tables are a faithful copy of the JSONL in the bucket -- exactly that, with
no column of ours at all. Everything the contract declares is NULLABLE here on
purpose: raw keeps whatever arrived, and a row that cannot be interpreted is dropped
by the semantic models, not by the load. Fields the contract does not declare are
dropped on load (``ignore_unknown_values``); the bytes stay in the bucket, so a new
field is picked up by adding it here and reloading.

There is no arrival timestamp on the row, and there was no way to have one. A
transfer names every column of the destination in its load job, and BigQuery writes
NULL rather than a default for any column a load job names -- so a
``DEFAULT CURRENT_TIMESTAMP()`` never fires. What arrived and when is asked of the
table instead: its last-modified time is metadata, free to read, and enough to say
whether a build is due (see warehouse.arrivals).
"""

from google.cloud.bigquery import SchemaField

#: Fields every record carries (see records.py in the collector).
_STAMP = [
    SchemaField("ts", "TIMESTAMP", description="Wall-clock time of the record, UTC."),
    SchemaField("uptime_seconds", "FLOAT", description="Seconds since the Pi booted."),
    SchemaField("boot_id", "STRING", description="Linux boot identifier."),
    SchemaField("ts_synced", "BOOL", description="Clock was NTP-synchronized when recorded."),
    SchemaField("type", "STRING", description="Stream discriminator."),
]


_PAYLOAD = SchemaField(
    "payload",
    "RECORD",
    description="Decoded body of a state report (cmd 4402).",
    fields=[
        SchemaField("setpoint_celsius", "INT64"),
        SchemaField("interior_temperature_celsius", "INT64"),
        SchemaField("display_unit", "STRING"),
        SchemaField("input_watts", "INT64"),
        SchemaField("usb_a_output_watts", "INT64"),
        SchemaField("usb_c_output_watts", "INT64"),
        SchemaField("charge_watts", "INT64"),
        SchemaField("discharge_watts", "INT64"),
        SchemaField("battery_state", "STRING"),
        SchemaField("state_of_charge_percent", "INT64"),
        SchemaField("protection_level", "STRING"),
        SchemaField("brightness", "STRING"),
        SchemaField("serial_number", "STRING"),
        SchemaField("battery_serial_number", "STRING"),
    ],
)

_ENVIRONMENT = SchemaField(
    "environment",
    "RECORD",
    description="Cabin air measured when the message arrived.",
    fields=[
        SchemaField("sensor", "STRING"),
        SchemaField("temperature_celsius", "FLOAT"),
        SchemaField("humidity_percent", "FLOAT"),
    ],
)

RAW_COOLER = [
    *_STAMP,
    SchemaField("model", "STRING", description="Decoder that produced payload."),
    SchemaField("address", "STRING", description="Bluetooth address of the cooler."),
    SchemaField("pattern", "STRING", description="Frame header pattern (hex)."),
    SchemaField("cmd", "STRING", description="Frame header command (hex)."),
    SchemaField("frames", "STRING", mode="REPEATED", description="The notifications, as received."),
    SchemaField("plain", "STRING", description="Message body in the clear (hex)."),
    _PAYLOAD,
    _ENVIRONMENT,
    SchemaField("error", "STRING", description="Why the message could not be read."),
]

RAW_EVENTS = [
    *_STAMP,
    SchemaField("kind", "STRING", description="What happened."),
    SchemaField("address", "STRING"),
    SchemaField("name", "STRING"),
    SchemaField("error", "STRING"),
    SchemaField("variant", "STRING"),
    SchemaField("mtu", "INT64"),
    SchemaField("chip", "STRING"),
    SchemaField("firmware", "STRING"),
    SchemaField("serial", "STRING"),
    SchemaField("secret", "STRING"),
    SchemaField("cmd", "STRING"),
    SchemaField("sensor", "STRING"),
    SchemaField("uploaded_chunk_count", "INT64"),
    SchemaField("uploaded_line_count", "INT64"),
]

SCHEMAS: dict[str, list[SchemaField]] = {"raw_cooler": RAW_COOLER, "raw_events": RAW_EVENTS}
