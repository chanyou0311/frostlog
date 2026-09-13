{{ config(materialized='table', contract={'enforced': true}) }}

-- The removable battery packs seen so far, plus the row that stands for "no
-- battery installed" so that a state update without a battery still has a key.

with seen as (

    select
        battery_serial_number as serial_number,
        min(updated_at) as first_seen_at
    from {{ ref('stg_collection__cooler_state_update') }}
    where battery_serial_number is not null
    group by battery_serial_number

)

select
    0 as battery_key,
    cast(null as string) as serial_number,
    true as is_absent,
    cast(null as timestamp) as first_seen_at

union all

select
    {{ frostlog_integer_key("serial_number") }} as battery_key,
    serial_number,
    false as is_absent,
    first_seen_at
from seen
