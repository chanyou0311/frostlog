{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_celsius', 'interior_temperature', 'celsius') }}
