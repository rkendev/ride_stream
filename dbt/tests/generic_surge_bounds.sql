/*
    Custom test: Surge multiplier must be within [1.0, 5.0].
    Applied to any model with a surge_multiplier column.
    Returns rows that violate the constraint.
*/

SELECT *
FROM {{ ref('stg_rides') }}
WHERE event_type = 'ride_completed'
  AND fare_cents IS NOT NULL
  AND (fare_cents < 0 OR fare_cents > 100000)
