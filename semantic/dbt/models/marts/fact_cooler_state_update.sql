{% set batch_boots = frostlog_batch_boot_ids(ref('stg_cooler_state_update')) %}

{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='state_update_key',
        incremental_predicates=[
            'DBT_INTERNAL_DEST.boot_id in ' ~ frostlog_id_list(batch_boots)
        ],
        partition_by={'field': 'partition_date', 'data_type': 'date'},
        cluster_by=['boot_id'],
        tags=['partitioned'],
        on_schema_change='fail',
        contract={'enforced': true},
    )
}}

-- The atomic fact: one row per state report from the cooler, with the cabin air at
-- that moment and the seconds the state is taken to hold. Everything else in the
-- model is an aggregate of this table.
--
-- The grain of a run is a boot, not a date, and the rows are merged on their key
-- rather than written over a date partition. The Pi's clock is wrong until NTP
-- catches up after a reboot, so the reports of a boot first land on whatever JST
-- date their recorded timestamp says; when a synced report of the same boot arrives
-- later, every earlier report of that boot is placed again and can end up on
-- another date. Overwriting date partitions would leave the copies on the old date
-- behind — two rows with the same state_update_key, a failing uniqueness test and
-- no build after that. A merge on the key moves the row instead.
--
-- `partition_date` is the JST date of `updated_at` as a DATE. It carries no meaning
-- beyond `date_key` and exists because BigQuery partitions on a column, not on an
-- expression.

with updates as (

    select * from {{ ref('stg_cooler_state_update') }}
    {% if is_incremental() %}
    where boot_id in {{ frostlog_id_list(batch_boots) }}
    {% endif %}

)

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
from updates u
left join {{ ref('dim_cooler') }} c
    on c.serial_number = u.serial_number
   and u.updated_at >= c.valid_from
   and (c.valid_to is null or u.updated_at < c.valid_to)
left join {{ ref('dim_battery') }} b
    on b.serial_number = u.battery_serial_number
