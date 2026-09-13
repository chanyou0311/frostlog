{#
  What a run reads out of the raw tables.

  The bucket delivers at least once: a chunk re-cut after a partial upload can carry
  the same record twice, and a redelivered event can bring a whole chunk again. Raw
  keeps whatever arrived, so agreeing on which copy counts is the reader's job, and
  this is where that is decided once for both staging models.
#}

{#- A chunk re-cut after a partial upload can deliver the same record twice; the
    copy that arrived last wins. Both staging models dedupe with this.

    uptime_seconds is a FLOAT64, and BigQuery refuses to partition by one — equality
    between floats is not a question it will answer for you. Its text is: the same
    stored value always prints the same way, so the spelling is stable, and it is the
    spelling the contract's own uniqueness rule already uses for this natural key. -#}
{% macro frostlog_latest_arrival() %}
    qualify row_number() over (
        partition by boot_id, cast(uptime_seconds as string)
        order by uploaded_at desc, source_key desc
    ) = 1
{% endmacro %}
