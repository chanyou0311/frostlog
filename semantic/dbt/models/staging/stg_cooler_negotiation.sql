-- What the cooler said about itself when the BLE session was set up: serial, chip
-- and firmware version. This is where dim_cooler's history comes from.

with negotiated as (

    select
        boot_id,
        uptime_seconds,
        ts,
        ts_synced,
        serial,
        address,
        chip,
        firmware,
        source_key,
        uploaded_at
    from {{ source('raw', 'raw_events') }}
    where kind = 'ble_negotiated'
      and boot_id is not null
      and uptime_seconds is not null
      and ts is not null
      and serial is not null

),

deduplicated as (

    select *
    from negotiated
    {{ frostlog_latest_arrival() }}

)

select
    d.boot_id,
    d.uptime_seconds,
    {{ frostlog_corrected_at(
        "d.ts", "d.uptime_seconds", "d.ts_synced",
        "r.reference_ts", "r.reference_uptime_seconds") }} as negotiated_at,
    d.serial as serial_number,
    d.address as bluetooth_address,
    d.chip,
    d.firmware as firmware_version
from deduplicated d
left join {{ ref('stg_clock_reference') }} r using (boot_id)
