/*
    Custom test: Distance must be non-negative and reasonable (< 500 km).
    Returns rows that violate the constraint.
*/

SELECT *
FROM {{ ref('stg_rides') }}
WHERE event_type = 'ride_completed'
  AND distance_km IS NOT NULL
  AND (distance_km < 0 OR distance_km > 500)
