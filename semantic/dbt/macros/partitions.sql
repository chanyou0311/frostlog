{#
  The dates a run rebuilds.

  A fact model is materialized with the insert_overwrite strategy and a static
  partition list, so a run replaces exactly the JST dates it was given and leaves
  every other partition untouched. The list comes from the `target_dates`
  variable, which the service fills with the dates the arrived chunk touches.

  When the variable is empty the list falls back to a single date on which no
  cooler existed. An empty list would make dbt delete nothing and insert
  everything, i.e. duplicate the table; a date without rows makes the run a
  no-op instead. Rebuilding everything is `dbt build --full-refresh`.
#}

{% macro frostlog_target_dates(days_before=0, days_after=0) %}
  {% set dates = var("target_dates", []) %}
  {% if dates | length == 0 %}
    {% do return(["1970-01-01"]) %}
  {% endif %}
  {% set expanded = [] %}
  {% for day in dates %}
    {% do frostlog_assert_date(day) %}
    {% for offset in range(-days_before, days_after + 1) %}
      {% set shifted = (modules.datetime.date.fromisoformat(day) + modules.datetime.timedelta(days=offset)).isoformat() %}
      {% if shifted not in expanded %}
        {% do expanded.append(shifted) %}
      {% endif %}
    {% endfor %}
  {% endfor %}
  {% do return(expanded | sort) %}
{% endmacro %}


{#- The same dates as BigQuery DATE literals, for `partitions` and for IN lists. -#}
{% macro frostlog_partitions(days_before=0, days_after=0) %}
  {% set literals = [] %}
  {% for day in frostlog_target_dates(days_before, days_after) %}
    {% do literals.append("date '" ~ day ~ "'") %}
  {% endfor %}
  {% do return(literals) %}
{% endmacro %}


{#- `partition_date in (...)` over those dates. -#}
{% macro frostlog_partition_filter(column="partition_date", days_before=0, days_after=0) %}
  {{ column }} in ({{ frostlog_partitions(days_before, days_after) | join(", ") }})
{% endmacro %}


{#- Refuse anything that is not a plain ISO date: the list is pasted into SQL. -#}
{% macro frostlog_assert_date(day) %}
  {% if day is not string or not modules.re.fullmatch("\\d{4}-\\d{2}-\\d{2}", day) %}
    {% do exceptions.raise_compiler_error("target_dates must be YYYY-MM-DD strings, got " ~ day) %}
  {% endif %}
{% endmacro %}
