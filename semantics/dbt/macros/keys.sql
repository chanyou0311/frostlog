{#
  Surrogate keys and the two derivations every fact needs.

  Keys are hashed from the natural key rather than numbered, because the
  dimensions are rebuilt in full on every run while the facts are rebuilt only
  for the dates that changed: a numbered key would move under the partitions
  that were not rebuilt. A hash of the natural key never moves.
#}

{#- An INT64 surrogate key. 0 is reserved for "absent", so keys start at 1. -#}
{% macro frostlog_integer_key(expression) -%}
  greatest(1, abs(ifnull(nullif(farm_fingerprint({{ expression }}), -9223372036854775808), 1)))
{%- endmacro %}


{#- A STRING surrogate key, for facts whose grain is a single record. -#}
{% macro frostlog_string_key(expression) -%}
  to_hex(sha256({{ expression }}))
{%- endmacro %}


{#-
  The time a record was really made.

  The Pi has no clock of its own: after a reboot in the car its wall clock is
  whatever it was when the power went, until NTP catches up. Every record does
  carry `uptime_seconds`, which is exact within a boot, so a record made before
  the clock was right is placed relative to the first record of the same boot
  that was made after it was.
-#}
{% macro frostlog_corrected_at(ts, uptime, synced, reference_ts, reference_uptime) -%}
  case
    when coalesce({{ synced }}, false) then {{ ts }}
    when {{ reference_ts }} is null then {{ ts }}
    else timestamp_add(
      {{ reference_ts }},
      interval cast(round(({{ uptime }} - {{ reference_uptime }}) * 1000) as int64) millisecond
    )
  end
{%- endmacro %}


{#- The dim_date key (yyyymmdd) of an instant, in Japan Standard Time. -#}
{% macro frostlog_date_key(timestamp_expression) -%}
  cast(format_timestamp('%Y%m%d', {{ timestamp_expression }}, 'Asia/Tokyo') as int64)
{%- endmacro %}
