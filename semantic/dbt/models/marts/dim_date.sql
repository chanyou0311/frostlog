{{ config(materialized='table', contract={'enforced': true}) }}

-- The calendar the whole model is read in: Japan Standard Time, one row per day.
-- Holidays come from a seed because they are announced, not computed.

with days as (

    select day
    from unnest(generate_date_array(
        date '{{ var("calendar_start") }}',
        date '{{ var("calendar_end") }}'
    )) as day

),

holidays as (

    select holiday_date from {{ ref('japanese_holidays') }}

)

select
    cast(format_date('%Y%m%d', d.day) as int64) as date_key,
    d.day as `date`,
    -- BigQuery counts Sunday as 1; ISO counts Monday as 1.
    mod(extract(dayofweek from d.day) + 5, 7) + 1 as day_of_week,
    extract(isoweek from d.day) as iso_week,
    extract(isoyear from d.day) as iso_year,
    extract(month from d.day) as month,
    mod(extract(dayofweek from d.day) + 5, 7) + 1 >= 6 as is_weekend,
    d.day in (select holiday_date from holidays) as is_japanese_holiday
from days d
