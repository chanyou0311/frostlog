{% set batch_boots = frostlog_batch_boot_ids() %}

{{
    config(
        materialized='incremental',
        incremental_strategy='insert_overwrite',
        partition_by={'field': 'partition_date', 'data_type': 'date'},
        tags=['partitioned'],
        on_schema_change='fail',
        contract={'enforced': true},
    )
}}

-- One row per JST hour, whether anything was recorded in it or not.
--
-- A state report holds for up to ten minutes, so its interval usually falls inside
-- one hour and sometimes straddles two. Each interval is cut at the hour boundaries
-- and the pieces are what the hour is made of: energy is the sum of watts times the
-- seconds inside the hour, conditions are averages weighted the same way, and
-- `covered_seconds` says how much of the hour those pieces actually cover.
--
-- Which dates a run recomputes is wider than the dates its chunk touched, because
-- the table has to stay dense: the reports of the boots in the batch may have moved
-- to another date since the last run (timestamp correction), a day on which nothing
-- was recorded appears in no chunk at all, and the day before a rebuilt one holds
-- the reports that reach into it.

with observed as (

    select
        min(updated_at) as first_at,
        max(timestamp_add(
            updated_at, interval cast(round(held_seconds * 1000) as int64) millisecond
        )) as last_at
    from {{ ref('fact_cooler_state_update') }}

),

{% if is_incremental() %}

wanted_days as (

    select day from unnest([{{ frostlog_partitions() | join(', ') }}]) as day

    union distinct

    -- Where this run's reports are now...
    select date(updated_at, 'Asia/Tokyo')
    from {{ ref('fact_cooler_state_update') }}
    where boot_id in {{ frostlog_id_list(batch_boots) }}

    union distinct

    -- ...and the date they were placed on before their timestamps were corrected.
    select date(updated_at_raw, 'Asia/Tokyo')
    from {{ ref('fact_cooler_state_update') }}
    where boot_id in {{ frostlog_id_list(batch_boots) }}

),

target_days as (

    select day from wanted_days

    union distinct

    -- A day the Pi spent switched off is in no chunk, so nothing would ever ask for
    -- its hours; without them the table has a hole and stops being dense.
    select day
    from unnest(generate_date_array(
        (select max(partition_date) from {{ this }}),
        (select max(day) from wanted_days)
    )) as day

),

{% else %}

target_days as (

    select day
    from observed,
        unnest(generate_date_array(
            date(first_at, 'Asia/Tokyo'), date(last_at, 'Asia/Tokyo')
        )) as day

),

{% endif %}

hours as (

    select hour_started_at
    from target_days,
        unnest(generate_timestamp_array(
            timestamp(day, 'Asia/Tokyo'),
            timestamp_sub(timestamp(date_add(day, interval 1 day), 'Asia/Tokyo'), interval 1 hour),
            interval 1 hour
        )) as hour_started_at
    -- The table starts at the first hour observed and stops at the hour the last
    -- held interval runs into, so that the tail of that interval is allocated too.
    cross join observed
    where hour_started_at between timestamp_trunc(first_at, hour)
                              and greatest(
                                      timestamp_trunc(first_at, hour),
                                      timestamp_trunc(
                                          timestamp_sub(last_at, interval 1 millisecond), hour
                                      )
                                  )

),

updates as (

    select
        cooler_key,
        battery_key,
        updated_at,
        held_seconds,
        timestamp_add(
            updated_at, interval cast(round(held_seconds * 1000) as int64) millisecond
        ) as held_until,
        state_of_charge_percent,
        discharge_watts,
        charge_watts,
        input_watts,
        usb_a_output_watts,
        usb_c_output_watts,
        ambient_temperature_celsius,
        ambient_humidity_percent,
        interior_temperature_celsius,
        setpoint_celsius,
        external_input,
        battery_state
    from {{ ref('fact_cooler_state_update') }}
    {% if is_incremental() %}
    -- The day before a rebuilt one too: its last report reaches into this one.
    where partition_date in (
        select day from target_days
        union distinct
        select date_sub(day, interval 1 day) from target_days
    )
    {% endif %}

),

allocation as (

    select
        h.hour_started_at,
        u.*,
        timestamp_diff(
            least(u.held_until, timestamp_add(h.hour_started_at, interval 1 hour)),
            greatest(u.updated_at, h.hour_started_at),
            millisecond
        ) / 1000.0 as overlap_seconds
    from hours h
    join updates u
        on u.updated_at < timestamp_add(h.hour_started_at, interval 1 hour)
       and u.held_until > h.hour_started_at

),

aggregated as (

    select
        hour_started_at,
        least(3600.0, sum(overlap_seconds)) as covered_seconds,
        count(*) as update_count,
        array_agg(cooler_key order by updated_at desc limit 1)[safe_offset(0)] as cooler_key,
        array_agg(battery_key order by updated_at desc limit 1)[safe_offset(0)] as battery_key,
        array_agg(state_of_charge_percent order by updated_at limit 1)[safe_offset(0)]
            as state_of_charge_start_percent,
        array_agg(state_of_charge_percent order by updated_at desc limit 1)[safe_offset(0)]
            as state_of_charge_end_percent,
        sum(discharge_watts * overlap_seconds / 3600) as discharged_watt_hours,
        sum(charge_watts * overlap_seconds / 3600) as charged_watt_hours,
        sum(input_watts * overlap_seconds / 3600) as input_watt_hours,
        sum(usb_a_output_watts * overlap_seconds / 3600) as usb_a_output_watt_hours,
        sum(usb_c_output_watts * overlap_seconds / 3600) as usb_c_output_watt_hours,
        safe_divide(
            sum(ambient_temperature_celsius * overlap_seconds),
            sum(if(ambient_temperature_celsius is null, 0, overlap_seconds))
        ) as ambient_temperature_celsius,
        safe_divide(
            sum(ambient_humidity_percent * overlap_seconds),
            sum(if(ambient_humidity_percent is null, 0, overlap_seconds))
        ) as ambient_humidity_percent,
        safe_divide(
            sum(interior_temperature_celsius * overlap_seconds), sum(overlap_seconds)
        ) as interior_temperature_celsius,
        safe_divide(
            sum(setpoint_celsius * overlap_seconds), sum(overlap_seconds)
        ) as setpoint_celsius,
        safe_divide(
            sum(if(external_input, overlap_seconds, 0)), sum(overlap_seconds)
        ) as external_input_ratio,
        safe_divide(
            sum(if(battery_state = 'charging', overlap_seconds, 0)), sum(overlap_seconds)
        ) as charging_ratio
    from allocation
    group by hour_started_at

)

select
    format_timestamp('%Y%m%d%H', h.hour_started_at, 'Asia/Tokyo') as hour_key,
    a.cooler_key,
    a.battery_key,
    {{ frostlog_date_key('h.hour_started_at') }} as date_key,
    date(h.hour_started_at, 'Asia/Tokyo') as partition_date,
    extract(hour from h.hour_started_at at time zone 'Asia/Tokyo') as hour_of_day,
    h.hour_started_at,
    a.state_of_charge_start_percent,
    a.state_of_charge_end_percent,
    a.state_of_charge_end_percent - a.state_of_charge_start_percent
        as state_of_charge_delta_percent,
    a.discharged_watt_hours,
    a.charged_watt_hours,
    a.input_watt_hours,
    a.usb_a_output_watt_hours,
    a.usb_c_output_watt_hours,
    a.ambient_temperature_celsius,
    a.ambient_humidity_percent,
    a.interior_temperature_celsius,
    a.setpoint_celsius,
    a.external_input_ratio,
    a.charging_ratio,
    coalesce(a.covered_seconds, 0.0) as covered_seconds,
    coalesce(a.update_count, 0) as update_count
from hours h
left join aggregated a using (hour_started_at)
