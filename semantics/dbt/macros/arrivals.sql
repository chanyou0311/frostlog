{#
  What a run reads out of the raw tables.

  The bucket delivers at least once: a chunk re-cut after a partial upload can carry
  the same record twice, and a redelivered event can bring a whole chunk again. Raw
  keeps whatever arrived, so agreeing on which copy counts is the reader's job, and
  this is where that is decided once for both staging models.
#}

{#-
  A chunk re-cut after a partial upload can deliver the same record twice, and the
  bucket is written at least once on purpose. Both staging models pick one copy
  with this.

  The copies are equals: the collection contract requires every copy of a message
  to carry the same frames, and that rule is checked against the bucket daily. So
  which one is kept does not matter -- only that the same one is kept every time a
  model is built, or a rebuild would churn rows that did not change.

  Ordering by the row's own JSON is what makes it repeatable without asking where
  the row came from; the caller names the relation so the whole row can be read. It
  used to order by the object name and its upload time, back when a query added those
  to every row; nothing writes them now, because writing them cost more than the load
  did. `_PARTITIONTIME` cannot take their place either: two copies of a report can
  land in the same transfer, and then share it.

  uptime_seconds is a FLOAT64, and BigQuery refuses to partition by one -- equality
  between floats is not a question it will answer for you. Its text is: the same
  stored value always prints the same way, so the spelling is stable, and it is the
  spelling the contract's own uniqueness rule already uses for this natural key.
-#}
{% macro frostlog_one_copy(relation) %}
    qualify row_number() over (
        partition by boot_id, cast(uptime_seconds as string)
        order by to_json_string({{ relation }})
    ) = 1
{% endmacro %}
