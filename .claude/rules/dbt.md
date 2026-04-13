# dbt Rules

## Core Principle
dbt is the single source of truth for ALL data transformation SQL. Application layer never builds queries dynamically. All SQL lives in dbt models, tested and versioned.

## Rule 3: ZERO f-String SQL
Never write SQL in Python code. Period.

```python
# BAD (v1 mistake)
query = f"SELECT * FROM rides WHERE driver_id = {driver_id}"
result = db.execute(query)

# GOOD (v2 standard)
# application/ride_service.py
rides = dbt_port.execute("rides_by_driver", {"driver_id": driver_id})

# dbt/models/live/rides_by_driver.sql
SELECT * FROM {{ source('staging', 'rides') }}
WHERE driver_id = '{{ var("driver_id") }}'
```

## Model Organization (Medallion Architecture)
Three layers: staging (bronze), intermediate (silver), fact/dimension (gold).

### Staging (Bronze): Raw data ingestion
Load data as-is from sources. Minimal transformation.

```sql
-- dbt/models/staging/stg_rides.sql
{{ config(
    materialized = 'table',
    indexes = [
        {'columns': ['ride_id'], 'unique': true}
    ]
) }}

SELECT
    ride_id,
    driver_id,
    passenger_id,
    request_datetime,
    pickup_location_lat,
    pickup_location_lon,
    dropoff_location_lat,
    dropoff_location_lon,
    distance_km,
    duration_minutes,
    fare_cents,
    created_at
FROM {{ source('raw', 'rides_ingest') }}
WHERE created_at >= CAST('{{ var("load_date") }}' AS DATE)
```

### Intermediate (Silver): Business logic, validation, enrichment
Apply business rules, join related entities, calculate derived fields.

```sql
-- dbt/models/intermediate/int_rides_with_driver_info.sql
{{ config(materialized = 'table') }}

SELECT
    r.ride_id,
    r.driver_id,
    d.driver_name,
    d.driver_rating,
    r.passenger_id,
    r.distance_km,
    r.duration_minutes,
    ROUND(r.fare_cents * 1.0 / 100, 2) AS fare_usd,
    CASE
        WHEN r.duration_minutes < 5 THEN 'short'
        WHEN r.duration_minutes < 15 THEN 'medium'
        ELSE 'long'
    END AS trip_duration_category,
    r.created_at
FROM {{ ref('stg_rides') }} r
LEFT JOIN {{ ref('stg_drivers') }} d ON r.driver_id = d.driver_id
WHERE r.created_at >= DATE_TRUNC('day', CURRENT_DATE - INTERVAL '30 days')
```

### Fact/Dimension (Gold): Analytics-ready tables
Aggregations, measures, dimensions. Consumed by BI and applications.

```sql
-- dbt/models/facts/fact_daily_ride_metrics.sql
{{ config(
    materialized = 'incremental',
    unique_key = ['metric_date', 'driver_id'],
    on_schema_change = 'fail'
) }}

SELECT
    DATE(r.created_at) AS metric_date,
    r.driver_id,
    COUNT(*) AS num_rides,
    ROUND(AVG(r.fare_cents) / 100.0, 2) AS avg_fare_usd,
    ROUND(SUM(r.distance_km), 2) AS total_distance_km,
    ROUND(AVG(r.duration_minutes), 0) AS avg_duration_minutes,
    ROUND(AVG(d.driver_rating), 2) AS avg_driver_rating
FROM {{ ref('int_rides_with_driver_info') }} r
LEFT JOIN {{ ref('stg_drivers') }} d ON r.driver_id = d.driver_id
WHERE r.created_at >= DATE_TRUNC('day', CURRENT_DATE - INTERVAL '1 days')
{% if execute %}
    AND r.created_at >= '{{ run_started_at }}'::DATE
{% endif %}
GROUP BY 1, 2

{% if execute and this.meta.get('materialized') == 'incremental' %}
    AND DATE(r.created_at) = CURRENT_DATE - INTERVAL '1 day'
{% endif %}
```

## Testing dbt Models (Rule 4)
Every model MUST have at least one test. dbt test failure blocks pipeline.

```yaml
# dbt/models/staging/stg_rides.yml
version: 2

models:
  - name: stg_rides
    description: "Raw rides staging table with basic structure validation"
    columns:
      - name: ride_id
        description: "Unique ride identifier"
        tests:
          - unique
          - not_null
      
      - name: driver_id
        description: "Driver ID"
        tests:
          - not_null
          - relationships:
              to: ref('stg_drivers')
              field: driver_id
      
      - name: distance_km
        description: "Distance traveled in kilometers"
        tests:
          - dbt_expectations.expect_column_values_to_be_in_type_list:
              column_type_list: ["numeric"]
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              max_value: 500
      
      - name: fare_cents
        description: "Fare in cents"
        tests:
          - not_null
          - accepted_values:
              values: [0]  # Allow free rides
              config:
                where: "fare_cents >= 0"

# Custom test
tests:
  - name: test_no_future_dates
    description: "Verify no created_at dates are in future"
    model: stg_rides
    columns:
      - name: created_at
        tests:
          - dbt_expectations.expect_column_values_to_be_less_than_or_equal_to:
              column_name: current_timestamp
```

## Jinja Macros (not raw SQL in models)
Reusable logic goes in macros, not repeated SQL.

```sql
-- dbt/macros/calculate_trip_duration_category.sql
{% macro calculate_trip_duration_category(duration_column) %}
    CASE
        WHEN {{ duration_column }} < 5 THEN 'short'
        WHEN {{ duration_column }} < 15 THEN 'medium'
        WHEN {{ duration_column }} < 30 THEN 'long'
        ELSE 'very_long'
    END
{% endmacro %}

-- Usage in model
-- dbt/models/intermediate/int_rides.sql
SELECT
    ride_id,
    driver_id,
    distance_km,
    duration_minutes,
    {{ calculate_trip_duration_category('duration_minutes') }} AS duration_category
FROM {{ ref('stg_rides') }}
```

## Source & Ref (no hardcoded table names)
All references go through dbt's ref() and source() macros. Sources reference MinIO (local) or S3 (prod) via Hive Metastore (Trino local) or Athena (prod).

```sql
-- dbt/models/staging/_sources.yml
version: 2

sources:
  - name: bronze
    description: "Raw data ingestion layer (MinIO bronze bucket)"
    database: hive
    schema: bronze
    tables:
      - name: ride_events
        description: "Raw ride events from external source"
        columns:
          - name: ride_id
            tests:
              - unique
              - not_null
      
      - name: driver_events
        description: "Raw driver events from external source"

  - name: silver
    description: "Transformed/intermediate layer (MinIO silver bucket)"
    database: hive
    schema: silver
    tables:
      - name: rides_enriched
        description: "Enriched rides with validation"
      
      - name: drivers_enriched
        description: "Enriched drivers with metrics"

# Usage in staging models (read from bronze)
-- dbt/models/staging/stg_rides.sql
SELECT * FROM {{ source('bronze', 'ride_events') }}

# Usage in intermediate models (read from staging, write to silver)
-- dbt/models/intermediate/int_rides_with_driver_info.sql
SELECT 
    r.ride_id,
    r.driver_id,
    d.driver_name,
    d.driver_rating
FROM {{ ref('stg_rides') }} r
LEFT JOIN {{ ref('stg_drivers') }} d ON r.driver_id = d.driver_id

# Usage in fact/dimension models (read from intermediate, write to gold)
-- dbt/models/facts/fact_daily_ride_metrics.sql
SELECT 
    DATE(r.created_at) AS metric_date,
    r.driver_id,
    COUNT(*) AS num_rides,
    ROUND(AVG(r.fare_cents) / 100.0, 2) AS avg_fare_usd
FROM {{ ref('int_rides_with_driver_info') }} r
GROUP BY 1, 2
```

## dbt_project.yml Configuration
Environment-aware profiles (local Trino, production Spark).

```yaml
# dbt/dbt_project.yml
name: ridestream
version: '2.0.0'
config-version: 2
profile: ridestream

model-paths: ["models"]
analysis-paths: ["analysis"]
test-paths: ["tests"]
data-paths: ["data"]
macro-paths: ["macros"]
snapshot-paths: ["snapshots"]
target-path: "target"
clean-targets:
  - "target"
  - "dbt_packages"

require-dbt-version: [">=1.7.0", "<2.0.0"]

vars:
  profile: "{{ env_var('RIDESTREAM_PROFILE', 'dev') }}"
  load_date: "{{ env_var('RIDESTREAM_LOAD_DATE', run_started_at | as_date) }}"

models:
  ridestream:
    staging:
      +materialized: table
      +schema: staging
      +pre-hook: "{{ log('Loading staging models', info=true) }}"
    
    intermediate:
      +materialized: table
      +schema: intermediate
    
    facts:
      +materialized: incremental
      +unique_key: ['metric_date', 'id']
      +schema: facts

seeds:
  ridestream:
    +schema: seeds
```

## profiles.yml (local vs production)
Different targets for different environments.

```yaml
# profiles.yml (in ~/.dbt/ or project root)
ridestream:
  target: "{{ env_var('RIDESTREAM_PROFILE', 'dev') }}"
  outputs:
    dev:
      type: trino
      method: none
      host: localhost
      port: 8888
      user: admin
      database: hive
      schema: ridestream_dev
      threads: 4
    
    prod:
      type: athena
      s3_staging_dir: s3://ridestream-athena-results/
      region_name: us-east-1
      database: ridestream_prod
      schema: gold
      threads: 8
```

## Adapter Configuration (Local vs Production)

### Local Development: dbt-Trino + Hive Metastore + MinIO
- **Adapter**: dbt-trino
- **Catalog**: hive (Trino catalog for table metadata)
- **Storage**: MinIO (S3-compatible object storage)
- **Port mapping**: Docker 8888 (host) → 8080 (Trino container)
- **Bucket structure**: bronze/ (raw) → silver/ (intermediate) → gold/ (facts)

```yaml
# Local profiles.yml (dev target)
dev:
  type: trino
  method: none
  host: localhost
  port: 8888          # Host port (mapped from container 8080)
  user: admin
  database: hive      # Hive Metastore catalog
  schema: ridestream_dev
  threads: 4
  
  # MinIO credentials (optional for Trino with Hive Metastore)
  # Trino discovers tables via Hive Metastore, backed by MinIO S3 API
```

### Production: dbt-Athena + AWS Glue Catalog + S3
- **Adapter**: dbt-athena (NOT dbt-spark)
- **Catalog**: AWS Glue Data Catalog (metadata)
- **Storage**: S3 (managed object storage)
- **Query results**: S3 staging directory
- **Schema structure**: gold schema for fact/dimension tables

```yaml
# Production profiles.yml (prod target)
prod:
  type: athena         # AWS Athena for distributed SQL
  s3_staging_dir: s3://ridestream-athena-results/
  region_name: us-east-1
  database: ridestream_prod  # Glue Data Catalog database
  schema: gold               # Schema containing fact/dimension tables
  threads: 8
  
  # AWS credentials via environment: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, or IAM role
```

### Table Metadata Flow
```
Local:
  MinIO bronze bucket → Hive Metastore → Trino (via dbt-trino) → dbt staging models

Production:
  S3 bronze prefix → Glue Data Catalog → Athena (via dbt-athena) → dbt staging models
```

## dbt Commands
```bash
# Local development (Trino + MinIO)
export RIDESTREAM_PROFILE=dev
dbt debug --profiles-dir .
dbt seed --profiles-dir .
dbt run --select staging --profiles-dir .
dbt test --select staging --profiles-dir .
dbt build --select staging --profiles-dir .

# Production (Athena + S3)
export RIDESTREAM_PROFILE=prod
export AWS_ACCESS_KEY_ID=<credentials>
export AWS_SECRET_ACCESS_KEY=<credentials>
dbt run --profiles-dir .
dbt test --profiles-dir .
dbt build --defer --state artifacts/prod --profiles-dir .

# Parse only (validate syntax)
dbt parse --profiles-dir .

# Generate docs
dbt docs generate --profiles-dir .
dbt docs serve --profiles-dir .
```

## Anti-Patterns (Never Do This)
- Hardcoded table names (use ref/source)
- Raw SQL outside .sql files (all SQL in dbt)
- Untested models (every model needs tests)
- f-strings in Python to generate dbt SQL
- Bare CTEs without documentation
- No lineage (always use ref/source)
- Mixing staging/intermediate/fact layer logic
- Incremental models without unique_key
- No dbt test execution in CI/CD

## Integration with Python Application
Application layer calls dbt-generated views through ports.

```python
# src/adapters/dbt/port.py
from abc import ABC, abstractmethod
from typing import List, Dict, Any

class DbtPort(ABC):
    @abstractmethod
    async def execute_model(
        self, 
        model_name: str, 
        variables: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Execute dbt model and return results."""
        pass

# src/adapters/dbt/aws.py
class DbtAdapterAws(DbtPort):
    async def execute_model(self, model_name: str, variables: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Query dbt-generated table/view
        query = f"SELECT * FROM ridestream_prod.{model_name}"
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

# src/application/ride_service.py
class RideApplicationService:
    async def get_daily_metrics(self, date: str) -> List[Dict]:
        metrics = await self.dbt_port.execute_model(
            "fact_daily_ride_metrics",
            {"metric_date": date}
        )
        return metrics
```
