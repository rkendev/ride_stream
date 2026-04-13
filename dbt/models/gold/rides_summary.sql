{{
    config(
        materialized='table'
    )
}}

/*
    Gold layer: Daily ride summary — aggregated by date, driver, passenger.
    Source: silver.stg_rides (completed events).
*/

WITH completed AS (
    SELECT
        ride_id,
        rider_id,
        driver_id,
        CAST(event_timestamp AS DATE) AS ride_date,
        fare_cents,
        fare_usd,
        distance_km
    FROM {{ ref('stg_rides') }}
    WHERE event_type = 'ride_completed'
)

SELECT
    ride_date,
    driver_id,
    rider_id AS passenger_id,
    COUNT(DISTINCT ride_id) AS rides_completed,
    ROUND(AVG(fare_cents), 0) AS avg_fare_cents,
    ROUND(AVG(fare_usd), 2) AS avg_fare_usd,
    ROUND(SUM(distance_km), 2) AS total_distance_km,
    ROUND(AVG(distance_km), 2) AS avg_distance_km,
    SUM(fare_cents) AS total_revenue_cents,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS _loaded_at
FROM completed
GROUP BY
    ride_date,
    driver_id,
    rider_id
