/*
    Singular test: No duplicate ride_ids in the rides_summary gold table.
    Returns rows that violate the uniqueness constraint.
*/

SELECT
    ride_date,
    driver_id,
    passenger_id,
    COUNT(*) AS cnt
FROM {{ ref('rides_summary') }}
GROUP BY ride_date, driver_id, passenger_id
HAVING COUNT(*) > 1
