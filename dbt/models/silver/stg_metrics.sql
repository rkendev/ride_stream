{{
    config(
        materialized='table'
    )
}}

/*
    Silver layer: Normalized 5-minute window metrics from Spark streaming.
    Source: This would normally read from the ride-metrics-5min Kafka topic
    via a Hive external table. For now, derives metrics from ride_events.
*/

WITH completed_rides AS (
    SELECT
        ride_id,
        event_timestamp,
        fare_cents,
        distance_meters,
        ROUND(distance_meters / 1000.0, 2) AS distance_km
    FROM {{ ref('stg_rides') }}
    WHERE event_type = 'ride_completed'
      AND fare_cents IS NOT NULL
),

windowed AS (
    SELECT
        DATE_TRUNC('hour', event_timestamp) AS metric_hour,
        COUNT(*) AS rides_completed_count,
        ROUND(AVG(fare_cents), 0) AS avg_fare_cents,
        ROUND(AVG(distance_km), 2) AS avg_distance_km,
        ROUND(SUM(fare_cents) / 100.0, 2) AS total_revenue_usd,
        MIN(event_timestamp) AS window_start,
        MAX(event_timestamp) AS window_end
    FROM completed_rides
    GROUP BY DATE_TRUNC('hour', event_timestamp)
)

SELECT
    metric_hour,
    rides_completed_count,
    avg_fare_cents,
    avg_distance_km,
    total_revenue_usd,
    window_start,
    window_end,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS _loaded_at
FROM windowed
