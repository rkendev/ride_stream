{{
    config(
        materialized='table'
    )
}}

/*
    Gold layer: Driver KPIs — rides completed, total earnings, avg distance.
    Source: silver.stg_rides (completed + accepted events).
*/

WITH completed AS (
    SELECT
        driver_id,
        ride_id,
        fare_cents,
        distance_km,
        event_timestamp
    FROM {{ ref('stg_rides') }}
    WHERE event_type = 'ride_completed'
      AND driver_id IS NOT NULL
),

driver_stats AS (
    SELECT
        driver_id,
        COUNT(DISTINCT ride_id) AS rides_completed,
        ROUND(SUM(fare_cents) / 100.0, 2) AS total_earnings_usd,
        ROUND(AVG(fare_cents) / 100.0, 2) AS avg_fare_usd,
        ROUND(SUM(distance_km), 2) AS total_distance_km,
        ROUND(AVG(distance_km), 2) AS avg_distance_km,
        MIN(event_timestamp) AS first_ride_at,
        MAX(event_timestamp) AS last_ride_at
    FROM completed
    GROUP BY driver_id
)

SELECT
    driver_id,
    rides_completed,
    total_earnings_usd,
    avg_fare_usd,
    total_distance_km,
    avg_distance_km,
    first_ride_at,
    last_ride_at,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS _loaded_at
FROM driver_stats
