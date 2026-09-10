{{ config(materialized='table', contract={'enforced': true}) }}

{{ frostlog_band('band_celsius', 'ambient_temperature', 'celsius') }}
