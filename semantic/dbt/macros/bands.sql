{#
  One band dimension out of the seed that holds all bands of its shape.

  Bands are joined by range — `lower <= value < upper`, an empty bound meaning
  infinity — so which band a measure falls in is decided when the question is
  asked, not when the fact is written. Changing a band never rewrites a fact.
#}

{% macro frostlog_band(seed, band, unit) %}
select
    band_key,
    label,
    lower_{{ unit }},
    upper_{{ unit }},
    sort_order
from {{ ref(seed) }}
where band = '{{ band }}'
{% endmacro %}
