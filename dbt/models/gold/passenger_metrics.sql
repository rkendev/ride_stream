{{
    config(
        materialized='table'
    )
}}

/*
    Gold layer: Passenger KPIs — rides taken, spending, frequency.
    Source: silver.stg_rides.
*/

WITH completed AS (
    SELECT
        rider_id AS passenger_id,
        ride_id,
        fare_cents,
        distance_km,
        event_timestamp,
        CAST(event_timestamp AS DATE) AS ride_date
    FROM {{ ref('stg_rides') }}
    WHERE event_type = 'ride_completed'
      AND rider_id IS NOT NULL
),

passenger_stats AS (
    SELECT
        passenger_id,
        COUNT(DISTINCT ride_id) AS rides_taken,
        ROUND(SUM(fare_cents) / 100.0, 2) AS total_spent_usd,
        ROUND(AVG(fare_cents) / 100.0, 2) AS avg_fare_usd,
        ROUND(AVG(distance_km), 2) AS avg_distance_km,
        COUNT(DISTINCT ride_date) AS active_days,
        MIN(event_timestamp) AS first_ride_at,
        MAX(event_timestamp) AS last_ride_at
    FROM completed
    GROUP BY passenger_id
),

repeat_riders AS (
    SELECT
        passenger_id,
        CASE
            WHEN rides_taken > 1 THEN true
            ELSE false
        END AS is_repeat_rider,
        ROUND(CAST(rides_taken AS DOUBLE) / NULLIF(active_days, 0), 2) AS rides_per_active_day
    FROM passenger_stats
)

SELECT
    ps.passenger_id,
    ps.rides_taken,
    ps.total_spent_usd,
    ps.avg_fare_usd,
    ps.avg_distance_km,
    ps.active_days,
    rr.is_repeat_rider,
    rr.rides_per_active_day,
    ps.first_ride_at,
    ps.last_ride_at,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS _loaded_at
FROM passenger_stats ps
JOIN repeat_riders rr ON ps.passenger_id = rr.passenger_id
