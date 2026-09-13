{#
  Which copy of a repeated record a run keeps.

  The bucket is written at least once on purpose: a chunk re-cut after a partial
  upload can deliver the same record twice, and raw keeps whatever arrived. Choosing
  between the copies is the reader's job, and the two staging models that carry a
  record forward as a row leave that choice here.

  The copies are not promised to be identical. The collection contract asks only that
  every copy of a cooler message carry the same frames (`repeated_messages_agree`) and
  every copy of an event the same `ts` (`repeated_events_agree`) — and `frames` is not
  even among the columns a caller carries this far. Whether two copies agree on the
  decoded payload, on the air around the cooler, or on `ts_synced` is not a question the
  contract answers. So the point is not that the choice is harmless; it is that the same
  copy wins every time, or a rebuild would churn rows that did not change.

  Ordering by the row's own JSON is what makes it repeatable without asking where the
  row came from: the caller names the relation so the whole row can be read, which
  orders copies that differ and leaves ties only between rows that are identical anyway.
  It used to order by the object name and its upload time, back when a query added those
  to every row; nothing writes them now, because writing them cost more than the load
  did. `_PARTITIONTIME` cannot take their place either: two copies of a report can land
  in the same transfer, and then share it.

  A record is identified by (boot_id, uptime_seconds); the events caller has already
  narrowed to one kind, which is the rest of that stream's natural key. uptime_seconds
  is a FLOAT64, and BigQuery refuses to partition by one — equality between floats is
  not a question it will answer for you. Its text is: the same stored value always
  prints the same way, so the spelling is stable, and it is the spelling the semantics
  contract's own uniqueness rule (`identity_unique`) uses for this natural key.
#}
{% macro frostlog_one_copy(relation) %}
    qualify row_number() over (
        partition by boot_id, cast(uptime_seconds as string)
        order by to_json_string({{ relation }})
    ) = 1
{% endmacro %}
