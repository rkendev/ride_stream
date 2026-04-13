/*
    Custom test: Fare must be non-negative for completed rides.
    Returns rows that violate the constraint.
*/

SELECT *
FROM {{ ref('stg_rides') }}
WHERE event_type = 'ride_completed'
  AND fare_cents IS NOT NULL
  AND fare_cents < 0
