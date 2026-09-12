{#
  Where a quarter-hour slot begins, and how far fact_cooler_snapshot reaches.

  The table starts at the first slot a state was observed in and stops at the slot the
  last held interval runs into, so that the tail of that interval is allocated too. The
  model asks for that span twice — once to lay the slots out and once to bound them —
  and the arithmetic behind it is the same each time, so it is written here once.
#}

{#- A timestamp moved back to the start of the quarter hour it falls in. -#}
{% macro frostlog_slot_start(moment) -%}
timestamp_trunc({{ moment }}, hour) + interval
    cast(div(extract(minute from {{ moment }}), 15) * 15 as int64) minute
{%- endmacro %}


{#- When a report stops holding: its own time plus the seconds it is taken to hold. -#}
{% macro frostlog_held_until(updated_at, held_seconds) -%}
timestamp_add(
    {{ updated_at }}, interval cast(round({{ held_seconds }} * 1000) as int64) millisecond
)
{%- endmacro %}


{% macro frostlog_observed_slots(fact) -%}
select
    first_at,
    last_at,
    {{ frostlog_slot_start('first_at') }} as first_slot,
    -- A millisecond back from the end, so an interval that stops exactly on a boundary
    -- does not claim the slot it never reaches into.
    greatest(
        {{ frostlog_slot_start('first_at') }},
        {{ frostlog_slot_start('timestamp_sub(last_at, interval 1 millisecond)') }}
    ) as last_slot
from (
    select
        min(updated_at) as first_at,
        max({{ frostlog_held_until('updated_at', 'held_seconds') }}) as last_at
    from {{ fact }}
)
{%- endmacro %}
