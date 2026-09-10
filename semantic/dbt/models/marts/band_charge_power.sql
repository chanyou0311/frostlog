{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_watts', 'charge_power', 'watts') }}
