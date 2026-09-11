{#
  The span of hours fact_cooler_hourly_snapshot covers.

  The table starts at the first hour a state was observed in and stops at the hour
  the last held interval runs into. Both the model and the hook that clears what a
  timestamp correction left behind ask for that span, so it is written once here.

  The hook is needed because correcting a boot's clock can empty the first or the
  last date the table had rows for, and a dynamic insert_overwrite run replaces
  only the partitions its result does have rows for — never one that ended up with
  none. Those rows would stay, with their energy, and `hours_are_dense` would then
  measure the table from a date nothing was recorded on.
#}

{% macro frostlog_observed_hours(fact) -%}
select
    min(updated_at) as first_at,
    max(timestamp_add(
        updated_at, interval cast(round(held_seconds * 1000) as int64) millisecond
    )) as last_at,
    timestamp_trunc(min(updated_at), hour) as first_hour,
    greatest(
        timestamp_trunc(min(updated_at), hour),
        timestamp_trunc(
            timestamp_sub(
                max(timestamp_add(
                    updated_at, interval cast(round(held_seconds * 1000) as int64) millisecond
                )),
                interval 1 millisecond
            ),
            hour
        )
    ) as last_hour
from {{ fact }}
{%- endmacro %}
