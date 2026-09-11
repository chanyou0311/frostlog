{{ config(materialized='table', contract={'enforced': true}) }}

-- The cooler and the history of its firmware (a type 2 slowly changing dimension).
--
-- Every BLE handshake reports the chip and the firmware version; a new row opens
-- when either of them (or the address) changes, and the row that was open closes
-- at that moment. A cooler that has only ever been seen in state reports — the
-- handshake event for it arrived in a chunk that is not here yet — still gets a
-- row, with an unknown firmware, so that no fact is left without a cooler.

with updates as (

    select serial_number, address as bluetooth_address, model, updated_at
    from {{ ref('stg_cooler_state_update') }}

),

-- The address a cooler was last seen with. Handshake events recorded before the
-- collector named the address on them take it from here, so an address that was
-- merely unrecorded does not open a new version.
addresses as (

    select serial_number, bluetooth_address
    from updates
    qualify row_number() over (partition by serial_number order by updated_at desc) = 1

),

negotiated as (

    select
        n.serial_number,
        coalesce(n.bluetooth_address, a.bluetooth_address) as bluetooth_address,
        n.chip,
        n.firmware_version,
        n.negotiated_at as observed_at
    from {{ ref('stg_cooler_negotiation') }} n
    left join addresses a using (serial_number)

),

unnegotiated as (

    select
        serial_number,
        bluetooth_address,
        cast(null as string) as chip,
        cast(null as string) as firmware_version,
        min(updated_at) as observed_at
    from updates
    where serial_number not in (select serial_number from negotiated)
    group by serial_number, bluetooth_address

),

observations as (

    select * from negotiated
    union all
    select * from unnegotiated

),

-- The cooler's model name is the decoder that read its messages.
named as (

    select serial_number, min(model) as model
    from updates
    group by serial_number

),

first_seen as (

    select serial_number, min(seen_at) as first_seen_at
    from (
        select serial_number, observed_at as seen_at from observations
        union all
        select serial_number, updated_at as seen_at from updates
    )
    group by serial_number

),

versioned as (

    select
        *,
        concat(
            coalesce(bluetooth_address, ''), '|',
            coalesce(chip, ''), '|',
            coalesce(firmware_version, '')
        ) as version
    from observations

),

changes as (

    select
        *,
        lag(version) over (
            partition by serial_number order by observed_at, version
        ) as previous_version
    from versioned

),

opened as (

    select
        *,
        row_number() over (partition by serial_number order by observed_at, version)
            as version_index
    from changes
    where previous_version is null or previous_version != version

)

select
    {{ frostlog_integer_key("concat(o.serial_number, '|', cast(o.version_index as string))") }}
        as cooler_key,
    o.serial_number,
    coalesce(n.model, 'everfrost') as model,
    o.bluetooth_address,
    o.chip,
    o.firmware_version,
    -- The first version is valid from the first sign of the cooler, whichever
    -- stream that was in, so that no state update falls outside every window.
    if(o.version_index = 1, f.first_seen_at, o.observed_at) as valid_from,
    lead(o.observed_at) over (partition by o.serial_number order by o.version_index)
        as valid_to,
    lead(o.observed_at) over (partition by o.serial_number order by o.version_index) is null
        as is_current
from opened o
left join named n using (serial_number)
left join first_seen f using (serial_number)
