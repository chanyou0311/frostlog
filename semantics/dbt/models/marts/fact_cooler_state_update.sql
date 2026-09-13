{{
    config(
        materialized='table',
        partition_by={'field': 'partition_date', 'data_type': 'date'},
        cluster_by=['boot_id'],
        tags=['partitioned'],
        on_schema_change='fail',
        contract={'enforced': true},
    )
}}

-- The atomic fact: one row per state report from the cooler, with the air around it at
-- that moment and the seconds the state is taken to hold. Everything else in the
-- model is an aggregate of this table.
--
-- Every run rebuilds the whole table. The Pi's clock is wrong until NTP catches up
-- after a reboot, so a synced report arriving later can move earlier reports of the
-- same boot to another date. Placing every row afresh removes its old placement too,
-- and accounts for a new neighbour changing its held_seconds or a late handshake
-- changing which cooler version applies.
--
-- On production-sized data a full build billed only 6% more than an incremental
-- one. BigQuery's minimum charge per query and referenced table leaves little to
-- save at this size, while the incremental machinery already caused a production
-- bug that inserted the same rows on every run. Replacing the table removes that
-- failure mode along with the machinery that made it possible.
--
-- `partition_date` is the JST date of `updated_at` as a DATE. It carries no meaning
-- beyond `date_key` and exists because BigQuery partitions on a column, not on an
-- expression.

select
    u.state_update_key,
    c.cooler_key,
    coalesce(b.battery_key, 0) as battery_key,
    {{ frostlog_date_key('u.updated_at') }} as date_key,
    date(u.updated_at, 'Asia/Tokyo') as partition_date,
    u.updated_at,
    u.updated_at_raw,
    u.timestamp_corrected,
    u.boot_id,
    u.uptime_seconds,
    u.source_key,
    u.external_input,
    u.battery_state,
    u.display_unit,
    u.protection_level,
    u.brightness,
    u.setpoint_celsius,
    u.interior_temperature_celsius,
    u.state_of_charge_percent,
    u.input_watts,
    u.usb_a_output_watts,
    u.usb_c_output_watts,
    u.charge_watts,
    u.discharge_watts,
    u.ambient_temperature_celsius,
    u.ambient_humidity_percent,
    u.held_seconds,
    -- The one place the integration rule is written (contract: watts × held_seconds / 3600).
    u.discharge_watts * u.held_seconds / 3600 as discharged_watt_hours,
    u.charge_watts * u.held_seconds / 3600 as charged_watt_hours,
    u.input_watts * u.held_seconds / 3600 as input_watt_hours
from {{ ref('stg_cooler_state_update') }} u
left join {{ ref('dim_cooler') }} c
    on c.serial_number = u.serial_number
   and u.updated_at >= c.valid_from
   and (c.valid_to is null or u.updated_at < c.valid_to)
left join {{ ref('dim_battery') }} b
    on b.serial_number = u.battery_serial_number
