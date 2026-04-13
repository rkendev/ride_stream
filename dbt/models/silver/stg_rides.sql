{{
    config(
        materialized='table'
    )
}}

/*
    Silver layer: Cleaned and deduplicated ride events.
    Source: bronze.ride_events (raw Kafka events in MinIO/S3 Parquet).

    Deduplication: Use ROW_NUMBER() over (ride_id, event_type, timestamp)
    to keep only the first occurrence of each event.
*/

WITH source_data AS (
    SELECT
        event_id,
        ride_id,
        event_type,
        -- Cast timestamp from VARCHAR (ISO-8601 in bronze) to TIMESTAMP
        -- without time zone for Hive compatibility.
        CAST(CAST(timestamp AS VARCHAR) AS TIMESTAMP) AS event_timestamp,
        rider_id,
        driver_id,
        CAST(latitude AS DOUBLE) AS latitude,
        CAST(longitude AS DOUBLE) AS longitude,
        CAST(fare_cents AS INTEGER) AS fare_cents,
        CAST(distance_meters AS DOUBLE) AS distance_meters
    FROM {{ source('bronze', 'ride_events') }}
    WHERE event_id IS NOT NULL
      AND ride_id IS NOT NULL
),

deduplicated AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY ride_id, event_type
            ORDER BY event_timestamp ASC
        ) AS row_num
    FROM source_data
)

SELECT
    event_id,
    ride_id,
    event_type,
    event_timestamp,
    rider_id,
    driver_id,
    latitude,
    longitude,
    fare_cents,
    distance_meters,
    ROUND(distance_meters / 1000.0, 2) AS distance_km,
    ROUND(fare_cents / 100.0, 2) AS fare_usd,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS _loaded_at
FROM deduplicated
WHERE row_num = 1
