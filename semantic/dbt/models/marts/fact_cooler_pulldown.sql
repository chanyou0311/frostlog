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

-- An accumulating snapshot: one row per pull-down, the episode of the interior
-- being brought from well above the setpoint down to it.
--
-- An episode starts at the first report that is 5 °C or more above the setpoint
-- while the one before it was not (or while there was no report before it worth
-- comparing to), and it ends at the first report within 1 °C of the setpoint —
-- reached — or when the battery is taken out or the recording stops for a quarter
-- of an hour — interrupted. An episode whose end has not been recorded yet is in
-- progress and keeps its milestones null.
--
-- The whole fact table is read, not only the dates being rebuilt: whether a report
-- starts an episode depends on the one before it, and where the episode ends may be
-- on the day after. Only the rows whose start falls on a rebuilt date are written.

with updates as (

    select
        state_update_key,
        cooler_key,
        battery_key,
        boot_id,
        updated_at,
        held_seconds,
        setpoint_celsius,
        interior_temperature_celsius,
        state_of_charge_percent,
        battery_state,
        external_input,
        discharge_watts,
        charge_watts,
        ambient_temperature_celsius
    from {{ ref('fact_cooler_state_update') }}

),

neighboured as (

    select
        *,
        lag(boot_id) over w as previous_boot_id,
        lag(updated_at) over w as previous_updated_at,
        lag(battery_state) over w as previous_battery_state,
        lag(setpoint_celsius) over w as previous_setpoint_celsius,
        lag(interior_temperature_celsius >= setpoint_celsius + 5) over w as previous_above,
        lead(boot_id) over w as next_boot_id,
        lead(updated_at) over w as next_updated_at
    from updates
    window w as (order by updated_at, state_update_key)

),

flagged as (

    select
        *,
        interior_temperature_celsius >= setpoint_celsius + 5 as above,
        interior_temperature_celsius <= setpoint_celsius + 1 as at_setpoint,
        -- Nothing usable to compare with: a fresh boot, a battery that was out, or a
        -- quarter of an hour of silence.
        previous_boot_id is null
            or previous_boot_id != boot_id
            or previous_battery_state = 'absent'
            or timestamp_diff(updated_at, previous_updated_at, second) >= 900
            as discontinuous,
        -- Nothing usable after this report either, so an episode cannot go on past it.
        battery_state = 'absent'
            or (next_updated_at is not null and next_boot_id != boot_id)
            or timestamp_diff(next_updated_at, updated_at, second) >= 900
            as breaks_after
    from neighboured

),

started as (

    select
        *,
        above and (discontinuous or not coalesce(previous_above, false)) as starts_episode,
        at_setpoint or coalesce(breaks_after, false) as terminal
    from flagged

),

sequenced as (

    select
        *,
        countif(starts_episode) over (
            order by updated_at, state_update_key
            rows between unbounded preceding and current row
        ) as episode_seq
    from started

),

numbered as (

    select
        *,
        row_number() over (
            partition by episode_seq order by updated_at, state_update_key
        ) as position
    from sequenced
    where episode_seq > 0

),

bounded as (

    select
        *,
        min(if(terminal, position, null)) over (partition by episode_seq) as end_position
    from numbered

),

-- The episode is the start report and everything up to and including the report
-- that ended it; what follows belongs to whatever comes next.
members as (

    select * from bounded
    where end_position is null or position <= end_position

),

episodes as (

    select
        episode_seq,
        max(end_position) as end_position,
        count(*) as update_count,
        array_agg(struct(
            state_update_key,
            cooler_key,
            battery_key,
            updated_at,
            setpoint_celsius,
            previous_setpoint_celsius,
            interior_temperature_celsius,
            state_of_charge_percent,
            discontinuous
        ) order by position limit 1)[safe_offset(0)] as opening,
        array_agg(struct(
            updated_at,
            state_of_charge_percent,
            at_setpoint,
            terminal
        ) order by position desc limit 1)[safe_offset(0)] as closing,
        -- The report that ended the episode is the moment it ended, so it holds for
        -- no time within it.
        sum(if(position = end_position, 0.0, held_seconds)) as covered_seconds,
        sum(if(position = end_position, 0.0, discharge_watts * held_seconds / 3600))
            as discharged_watt_hours,
        sum(if(position = end_position, 0.0, charge_watts * held_seconds / 3600))
            as charged_watt_hours,
        safe_divide(
            sum(if(position = end_position, 0.0,
                   ambient_temperature_celsius * held_seconds)),
            sum(if(position = end_position or ambient_temperature_celsius is null,
                   0.0, held_seconds))
        ) as ambient_temperature_celsius,
        safe_divide(
            sum(if(position = end_position, 0.0, if(external_input, held_seconds, 0.0))),
            sum(if(position = end_position, 0.0, held_seconds))
        ) as external_input_ratio
    from members
    group by episode_seq

)

select
    e.opening.state_update_key as pulldown_key,
    e.opening.cooler_key,
    e.opening.battery_key,
    {{ frostlog_date_key('e.opening.updated_at') }} as date_key,
    date(e.opening.updated_at, 'Asia/Tokyo') as partition_date,
    e.opening.updated_at as started_at,
    if(e.closing.terminal and e.closing.at_setpoint, e.closing.updated_at, null) as reached_at,
    if(e.closing.terminal, e.closing.updated_at, null) as ended_at,
    case
        when e.opening.discontinuous then 'start_up'
        when e.opening.previous_setpoint_celsius > e.opening.setpoint_celsius
            then 'setpoint_change'
        else 'rise'
    end as `trigger`,
    case
        when e.closing.terminal and e.closing.at_setpoint then 'reached'
        when e.closing.terminal then 'interrupted'
    end as outcome,
    e.opening.interior_temperature_celsius as interior_temperature_start_celsius,
    e.opening.setpoint_celsius as setpoint_celsius,
    e.opening.state_of_charge_percent as state_of_charge_start_percent,
    if(
        e.closing.terminal,
        e.closing.state_of_charge_percent - e.opening.state_of_charge_percent,
        null
    ) as state_of_charge_delta_percent,
    if(
        e.closing.terminal and e.closing.at_setpoint,
        timestamp_diff(e.closing.updated_at, e.opening.updated_at, millisecond) / 1000.0,
        null
    ) as duration_seconds,
    if(
        e.closing.terminal,
        timestamp_diff(e.closing.updated_at, e.opening.updated_at, millisecond) / 1000.0,
        null
    ) as elapsed_seconds,
    e.discharged_watt_hours,
    e.charged_watt_hours,
    e.ambient_temperature_celsius,
    e.external_input_ratio,
    e.covered_seconds,
    e.update_count
from episodes e

{% if is_incremental() %}
where {{ frostlog_partition_filter("date(e.opening.updated_at, 'Asia/Tokyo')") }}
{% endif %}
