-- The anchor used to repair the timestamps of one boot.
--
-- The first record of a boot that was made after NTP had synchronized fixes the
-- relation between the Pi's uptime and real time for the whole boot. Both streams
-- are looked at, because the anchor is often an event (the BLE connection) rather
-- than a cooler message.

with recorded as (

    select boot_id, ts, uptime_seconds
    from {{ source('raw', 'raw_cooler') }}
    where ts_synced

    union all

    select boot_id, ts, uptime_seconds
    from {{ source('raw', 'raw_events') }}
    where ts_synced

),

ranked as (

    select
        boot_id,
        ts as reference_ts,
        uptime_seconds as reference_uptime_seconds,
        row_number() over (partition by boot_id order by uptime_seconds) as position
    from recorded
    where boot_id is not null and ts is not null and uptime_seconds is not null

)

select boot_id, reference_ts, reference_uptime_seconds
from ranked
where position = 1
