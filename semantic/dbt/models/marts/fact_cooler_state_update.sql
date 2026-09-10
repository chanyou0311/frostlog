{{
    config(
        materialized='incremental',
        incremental_strategy='insert_overwrite',
        partition_by={'field': 'partition_date', 'data_type': 'date'},
        partitions=frostlog_partitions(),
        tags=['partitioned'],
        on_schema_change='fail',
        contract={'enforced': true},
    )
}}

-- The atomic fact: one row per state report from the cooler, with the cabin air at
-- that moment and the seconds the state is taken to hold. Everything else in the
-- model is an aggregate of this table.
--
-- `partition_date` is the JST date of `updated_at` as a DATE. It carries no meaning
-- beyond `date_key` and exists because BigQuery partitions on a column, not on an
-- expression, and rebuilding is done one JST date at a time.

with updates as (

    select * from {{ ref('stg_cooler_state_update') }}

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
    u.held_seconds
from updates u
left join {{ ref('dim_cooler') }} c
    on c.serial_number = u.serial_number
   and u.updated_at >= c.valid_from
   and (c.valid_to is null or u.updated_at < c.valid_to)
left join {{ ref('dim_battery') }} b
    on b.serial_number = u.battery_serial_number

{% if is_incremental() %}
where {{ frostlog_partition_filter("date(u.updated_at, 'Asia/Tokyo')") }}
{% endif %}
