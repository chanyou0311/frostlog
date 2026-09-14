-- The state reports, one row each, with their timestamps repaired and the seconds
-- each state is taken to hold. A report is what the cooler sends by itself (cmd
-- 4402) or what it answers the gateway's request with after connecting (cmd 4840):
-- the same body under two headers, and the first state of a connection is usually
-- the answer, since the cooler does not always speak first.
--
-- A report whose body could not be decoded, or which is missing a value the
-- dimensional model requires, is dropped here rather than carried as nulls: the
-- bytes stay in raw and can be decoded again later.

with decoded as (

    select
        boot_id,
        uptime_seconds,
        ts,
        ts_synced,
        model,
        address,
        payload,
        environment
    from {{ source('raw', 'raw_cooler') }}
    where cmd in ('4402', '4840')
      and boot_id is not null
      and uptime_seconds is not null
      and ts is not null
      and payload.serial_number is not null
      and payload.setpoint_celsius is not null
      and payload.interior_temperature_celsius is not null
      and payload.state_of_charge_percent is not null
      and payload.input_watts is not null
      and payload.charge_watts is not null
      and payload.discharge_watts is not null
      and payload.usb_a_output_watts is not null
      and payload.usb_c_output_watts is not null
      and payload.battery_state is not null
      and payload.display_unit is not null
      and payload.protection_level is not null
      and payload.brightness is not null

),

-- A message is identified by (boot_id, uptime_seconds). The same message reaches
-- BigQuery twice if a chunk was re-cut; the copies are equals, so one is chosen
-- the same way every build.
deduplicated as (

    select *
    from decoded as d
    {{ frostlog_one_copy('d') }}

),

corrected as (

    select
        d.boot_id,
        d.uptime_seconds,
        d.ts as updated_at_raw,
        {{ frostlog_corrected_at(
            "d.ts", "d.uptime_seconds", "d.ts_synced",
            "r.reference_ts", "r.reference_uptime_seconds") }} as updated_at,
        d.model,
        d.address,
        d.payload,
        d.environment
    from deduplicated d
    left join {{ ref('stg_collection__clock_reference') }} r using (boot_id)

),

-- How long a state is taken to hold: until the next report of the same cooler, at
-- most the ten minutes after which the cooler repeats itself anyway. The last
-- report before a silence therefore counts for ten minutes, not for the silence.
held as (

    select
        *,
        greatest(0.0, least(600.0, coalesce(
            lead(uptime_seconds) over (partition by boot_id, address order by uptime_seconds)
                - uptime_seconds,
            600.0
        ))) as held_seconds
    from corrected

)

select
    -- The key is made of the boot and the uptime, never of the timestamp: a record
    -- whose clock was wrong keeps its identity when its time is later corrected.
    {{ frostlog_string_key("concat(boot_id, '/', cast(uptime_seconds as string))") }}
        as state_update_key,
    boot_id,
    uptime_seconds,
    updated_at,
    updated_at_raw,
    updated_at != updated_at_raw as timestamp_corrected,
    model,
    address,
    held_seconds,
    payload.serial_number as serial_number,
    payload.battery_serial_number as battery_serial_number,
    payload.display_unit as display_unit,
    payload.protection_level as protection_level,
    payload.brightness as brightness,
    payload.battery_state as battery_state,
    payload.setpoint_celsius as setpoint_celsius,
    payload.interior_temperature_celsius as interior_temperature_celsius,
    payload.state_of_charge_percent as state_of_charge_percent,
    payload.input_watts as input_watts,
    payload.usb_a_output_watts as usb_a_output_watts,
    payload.usb_c_output_watts as usb_c_output_watts,
    payload.charge_watts as charge_watts,
    payload.discharge_watts as discharge_watts,
    payload.input_watts > 0 as external_input,
    environment.temperature_celsius as ambient_temperature_celsius,
    environment.humidity_percent as ambient_humidity_percent
from held
