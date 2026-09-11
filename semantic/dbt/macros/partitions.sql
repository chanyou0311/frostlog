{#
  What a run rebuilds.

  The service passes the JST dates the arrived chunk touches in `target_dates`.
  Two things follow from them: which raw rows are in the run's batch, and which
  dates the aggregates have to be recomputed for.

  The batch is not a set of dates but a set of boots. A record made before NTP had
  synchronized carries a wrong timestamp until a synced record of the same boot
  arrives, and correcting it moves the record to another JST date; only "every row
  of every boot this chunk touched" is closed under that move.

  When `target_dates` is empty the date list falls back to a day on which no cooler
  existed, so an incremental run without it is a no-op rather than a full rebuild.
  Rebuilding everything is `dbt build --full-refresh`.
#}

{% macro frostlog_target_dates() %}
  {% set dates = var("target_dates", []) %}
  {% if dates | length == 0 %}
    {% do return(["1970-01-01"]) %}
  {% endif %}
  {% for day in dates %}
    {% do frostlog_assert_date(day) %}
  {% endfor %}
  {% do return(dates | unique | sort) %}
{% endmacro %}


{#- The same dates as BigQuery DATE literals, for IN lists. -#}
{% macro frostlog_partitions() %}
  {% set literals = [] %}
  {% for day in frostlog_target_dates() %}
    {% do literals.append("date '" ~ day ~ "'") %}
  {% endfor %}
  {% do return(literals) %}
{% endmacro %}


{#- Refuse anything that is not a plain ISO date: the list is pasted into SQL. -#}
{% macro frostlog_assert_date(day) %}
  {% if day is not string or not modules.re.fullmatch("\\d{4}-\\d{2}-\\d{2}", day) %}
    {% do exceptions.raise_compiler_error("target_dates must be YYYY-MM-DD strings, got " ~ day) %}
  {% endif %}
{% endmacro %}


{#-
  The boots of the run's batch, read from the warehouse while the model compiles.

  They cannot be a subquery: an insert_overwrite partition filter and a merge
  condition both have to be expressions BigQuery can evaluate before it reads the
  table, or nothing is pruned. So the ids are fetched once and written into the
  SQL as literals. Parsing and unit tests never query — there is no run to batch.
-#}
{% macro frostlog_batch_boot_ids() %}
  {% if execute and is_incremental() %}
    {#- Straight from the raw table (three columns), not through the staging view,
        which decodes and dedupes the whole history for a question about two dates. -#}
    {% set query %}
      select distinct boot_id
      from {{ source('raw', 'raw_cooler') }}
      where cmd = '4402'
        and boot_id is not null
        and date(regexp_extract(source_key, r'dt=(\d{4}-\d{2}-\d{2})'))
            in ({{ frostlog_partitions() | join(', ') }})
    {% endset %}
    {% do return(frostlog_checked_ids(run_query(query).columns[0].values())) %}
  {% endif %}
  {% do return([]) %}
{% endmacro %}


{#- Boot ids end up inside SQL literals, so they have to look like boot ids. -#}
{% macro frostlog_checked_ids(values) %}
  {% set checked = [] %}
  {% for value in values %}
    {% if value is string and modules.re.fullmatch("[A-Za-z0-9_.:-]{1,64}", value) %}
      {% do checked.append(value) %}
    {% else %}
      {% do exceptions.raise_compiler_error("not a usable boot_id: " ~ value) %}
    {% endif %}
  {% endfor %}
  {% do return(checked) %}
{% endmacro %}


{#- Those ids as an IN list. An empty batch matches nothing, as it should. -#}
{% macro frostlog_id_list(values) -%}
  {%- if values | length == 0 -%}
    (null)
  {%- else -%}
    ({% for value in values %}'{{ value }}'{{ ", " if not loop.last }}{% endfor %})
  {%- endif -%}
{%- endmacro %}


{#- A chunk re-cut after a partial upload can deliver the same record twice; the
    copy that arrived last wins. Both staging models dedupe with this. -#}
{% macro frostlog_latest_arrival() %}
    qualify row_number() over (
        partition by boot_id, uptime_seconds
        order by uploaded_at desc, source_key desc
    ) = 1
{% endmacro %}
