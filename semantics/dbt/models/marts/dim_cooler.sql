{{ config(materialized='table', contract={'enforced': true}) }}

-- The cooler and the history of its firmware (a type 2 slowly changing dimension).
--
-- Every BLE handshake reports the chip and the firmware version; a new row opens
-- when either of them (or the address) changes, and the row that was open closes
-- at that moment. Every cooler also keeps a row with an unknown firmware, from
-- its first sign until its first handshake. That handshake may arrive in a later
-- chunk, after a fact from another boot has already taken the unknown row's key;
-- closing the row must not take the key away from a fact that this run leaves alone.

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

-- The cooler's model name is the decoder that read its messages.
named as (

    select serial_number, min(model) as model
    from updates
    group by serial_number

),

first_seen as (

    select serial_number, min(seen_at) as first_seen_at
    from (
        select serial_number, observed_at as seen_at from negotiated
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
    from negotiated

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
        row_number() over (partition by serial_number, version order by observed_at)
            as version_episode
    from changes
    where previous_version is null or previous_version != version

),

-- This row belongs to the cooler, not to an address it happened to report. A
-- later state report can fill in or change that address without replacing the
-- unknown key. It also belongs to a cooler known only from handshakes: in that
-- case its window is empty, but keeping it means the order in which the two
-- streams arrive cannot decide whether the key exists.
unnegotiated as (

    select
        f.serial_number,
        a.bluetooth_address,
        cast(null as string) as chip,
        cast(null as string) as firmware_version,
        f.first_seen_at as observed_at,
        'unnegotiated' as version,
        1 as version_episode,
        true as is_unnegotiated
    from first_seen f
    left join addresses a using (serial_number)

),

episodes as (

    select
        serial_number, bluetooth_address, chip, firmware_version, observed_at,
        version, version_episode, false as is_unnegotiated
    from opened
    union all
    select * from unnegotiated

),

ordered as (

    select
        *,
        -- When the first sign is the handshake itself, the unknown row closes
        -- exactly where it opens. It must sort first even if the address or the
        -- firmware is missing, so a report at that instant joins only the handshake.
        row_number() over (
            partition by serial_number order by observed_at, is_unnegotiated desc, version
        ) as version_index
    from episodes

)

select
    -- The first handshake we received is not necessarily the first one that
    -- happened. An earlier handshake with the same version, or a corrected clock,
    -- can move the opening time without replacing the firmware. Hashing that time
    -- would change the identity of the same firmware episode. Count only episodes
    -- of this particular version, so inserting another firmware does not renumber
    -- it, while returning to an old firmware still gets a distinct key.
    --
    -- A repeated version can still lose its later key if a clock correction merges
    -- its two visits: A -> B -> A becomes A -> A -> B. For firmware on this consumer
    -- cooler that takes a downgrade as well as the clock correction. The facts are
    -- rebuilt from the same history on every run, so they resolve the resulting
    -- episodes without needing to retain vanished ones.
    {{ frostlog_integer_key("concat(o.serial_number, '|', o.version, '|', cast(o.version_episode as string))") }}
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
from ordered o
left join named n using (serial_number)
left join first_seen f using (serial_number)
