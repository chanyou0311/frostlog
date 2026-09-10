{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_watts', 'discharge_power', 'watts') }}
