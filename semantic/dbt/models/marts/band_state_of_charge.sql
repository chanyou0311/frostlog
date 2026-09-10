{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_percent', 'state_of_charge', 'percent') }}
