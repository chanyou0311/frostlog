{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_watts', 'input_power', 'watts') }}
