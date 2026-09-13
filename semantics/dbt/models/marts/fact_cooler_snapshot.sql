{{
    config(
        materialized='table',
        partition_by={'field': 'partition_date', 'data_type': 'date'},
        tags=['partitioned'],
        on_schema_change='fail',
        contract={'enforced': true},
    )
}}

-- One row per quarter hour (JST), whether anything was recorded in it or not.
--
-- A state report holds for up to ten minutes, so its interval often straddles a slot
-- boundary. Each interval is cut at those boundaries and the pieces are what a slot
-- is made of: energy is the sum of watts times the seconds inside the slot,
-- conditions are averages weighted the same way, and `covered_seconds` says how much
-- of the slot those pieces actually cover.
--
-- A quarter hour rather than an hour because this is what consumers draw from, and a
-- two-hour trip is two points of an hourly table. Cutting the intervals is the part
-- that cannot be done again downstream; folding four slots into an hour can.
--
-- Built whole every run, like fact_cooler_pulldown and for the same reason. Dense is
-- a property of the table, not of a partition, and a correction that moves a report
-- to another date moves the hole with it; keeping that true incrementally took three
-- separate mechanisms — a widened date list, a look at the day before, and a delete
-- of what a rebuild had emptied — each of which had to agree with the other two.
-- The transform runs hourly rather than on every chunk, so this work is shared
-- across the arrivals between runs.

with observed as (

    {{ frostlog_observed_slots(ref('fact_cooler_state_update')) }}

),

target_days as (

    select day
    from observed,
        unnest(generate_date_array(
            date(first_at, 'Asia/Tokyo'), date(last_at, 'Asia/Tokyo')
        )) as day

),

slots as (

    select slot_started_at
    from target_days,
        unnest(generate_timestamp_array(
            timestamp(day, 'Asia/Tokyo'),
            timestamp_sub(
                timestamp(date_add(day, interval 1 day), 'Asia/Tokyo'), interval 15 minute
            ),
            interval 15 minute
        )) as slot_started_at
    -- The table starts at the first slot observed and stops at the slot the last
    -- held interval runs into, so that the tail of that interval is allocated too.
    cross join observed
    where slot_started_at between observed.first_slot and observed.last_slot

),

updates as (

    select
        cooler_key,
        battery_key,
        updated_at,
        held_seconds,
        {{ frostlog_held_until('updated_at', 'held_seconds') }} as held_until,
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

),

allocation as (

    select
        s.slot_started_at,
        u.*,
        timestamp_diff(
            least(u.held_until, timestamp_add(s.slot_started_at, interval 15 minute)),
            greatest(u.updated_at, s.slot_started_at),
            millisecond
        ) / 1000.0 as overlap_seconds
    from slots s
    join updates u
        on u.updated_at < timestamp_add(s.slot_started_at, interval 15 minute)
       and u.held_until > s.slot_started_at

),

aggregated as (

    select
        slot_started_at,
        least(900.0, sum(overlap_seconds)) as covered_seconds,
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
    group by slot_started_at

)

select
    format_timestamp('%Y%m%d%H%M', s.slot_started_at, 'Asia/Tokyo') as slot_key,
    a.cooler_key,
    a.battery_key,
    {{ frostlog_date_key('s.slot_started_at') }} as date_key,
    date(s.slot_started_at, 'Asia/Tokyo') as partition_date,
    extract(hour from s.slot_started_at at time zone 'Asia/Tokyo') as hour_of_day,
    extract(minute from s.slot_started_at at time zone 'Asia/Tokyo') as minute_of_hour,
    s.slot_started_at,
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
from slots s
left join aggregated a using (slot_started_at)
