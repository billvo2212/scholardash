# Phase 2 — dbt Transforms: Raw → Staging → Marts

**Goal:** Turn raw NSF data into clean, queryable mart tables that answer BQ-4 and BQ-5. By the end, you can query `mart_funding_by_field` and get real answers about funding distribution.

**Duration:** ~1 week  
**Prerequisite:** Phase 1 complete. NSF raw data in DuckDB.

---

## Why dbt? The Core Mental Model

Without dbt, you have a folder of SQL files with names like `transform_step1.sql`, `final_query.sql`, `final_query_v2.sql`. Nobody knows which order to run them. Nobody knows which table feeds which. When NSF changes their schema, you update one file and break three others without knowing.

dbt solves this with:
1. **Dependency graph:** `{{ ref('stg_nsf_awards') }}` in a model tells dbt "this depends on stg_nsf_awards — run it first"
2. **Testing:** declarative assertions on data quality, run with `dbt test`
3. **Documentation:** `dbt docs generate` builds a browsable data catalog
4. **Materialization control:** staging = views (cheap), marts = tables (fast to query)

---

## Step 2.1 — Install dbt for DuckDB

```bash
pip install dbt-duckdb

# Verify
dbt --version
# Should show: dbt-core x.x.x, dbt-duckdb x.x.x
```

**Important:** Install `dbt-duckdb`, not `dbt-core` alone. The DuckDB adapter is a separate package.

---

## Step 2.2 — Initialize dbt Project

```bash
# From project root
cd transform
dbt init scholarhub --skip-profile-setup
cd scholarhub

# Clean up default example files
rm -rf models/example
```

Create `transform/scholarhub/profiles.yml`:

```yaml
# transform/scholarhub/profiles.yml
# This tells dbt HOW to connect to your database.
# profiles.yml lives OUTSIDE git (it contains credentials in production).
# For local dev, it's safe to commit since we're using a local file path.

scholarhub:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "../../warehouse/scholarhub.duckdb"   # relative to profiles.yml
      schema: analytics     # dbt creates this schema in DuckDB
      threads: 4

    prod:
      type: duckdb
      path: "../../warehouse/scholarhub_prod.duckdb"
      schema: analytics
      threads: 4
```

Update `transform/scholarhub/dbt_project.yml`:

```yaml
# transform/scholarhub/dbt_project.yml
name: scholarhub
version: "1.0.0"
config-version: 2

profile: scholarhub

model-paths: ["models"]
test-paths: ["tests"]
seed-paths: ["seeds"]
macro-paths: ["macros"]
snapshot-paths: ["snapshots"]

target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  scholarhub:
    # Staging: views are cheap, don't persist rows
    # Recomputed on every query — no storage cost
    staging:
      +materialized: view
      +schema: staging

    # Intermediate: tables because they're reused by multiple marts
    intermediate:
      +materialized: table
      +schema: intermediate

    # Marts: tables, optimized for dashboard queries
    marts:
      +materialized: table
      +schema: marts

    # Dimensions: tables, relatively static reference data
    dimensions:
      +materialized: table
      +schema: dimensions
```

Create the model directory structure:

```bash
mkdir -p models/staging
mkdir -p models/intermediate
mkdir -p models/marts
mkdir -p models/dimensions
touch models/staging/.gitkeep
touch models/intermediate/.gitkeep
touch models/marts/.gitkeep
touch models/dimensions/.gitkeep
```

Test the connection:
```bash
cd transform/scholarhub
dbt debug
# Should output: "All checks passed!" at the end
```

---

## Step 2.3 — Dimension: `dim_date`

The date dimension is the backbone of all time-series analysis. Generate it once, use it everywhere. This is standard Kimball practice — never compute date attributes (quarter, semester, fiscal year) in mart queries. Compute them here once.

Create `models/dimensions/dim_date.sql`:

```sql
-- models/dimensions/dim_date.sql
-- Generates a complete date spine from 2010-01-01 to 2035-12-31.
-- Why 2010? NSF data before 2010 has quality issues.
-- Why 2035? Grant end dates extend into the future.

WITH date_spine AS (
    SELECT
        (DATE '2010-01-01' + INTERVAL (n) DAY)::DATE AS full_date
    FROM generate_series(0, 9131) AS t(n)   -- 25 years * 365.25 days ≈ 9131
),

date_attributes AS (
    SELECT
        full_date,
        CAST(strftime(full_date, '%Y%m%d') AS INTEGER)  AS date_sk,   -- surrogate key: 20240115
        EXTRACT(YEAR  FROM full_date)::INTEGER           AS year,
        EXTRACT(MONTH FROM full_date)::INTEGER           AS month,
        EXTRACT(DAY   FROM full_date)::INTEGER           AS day,
        EXTRACT(QUARTER FROM full_date)::INTEGER         AS quarter,
        strftime(full_date, '%B')                        AS month_name, -- 'January'
        strftime(full_date, '%A')                        AS day_name,   -- 'Monday'
        EXTRACT(DOW FROM full_date)::INTEGER             AS day_of_week,-- 0=Sunday
        EXTRACT(WEEK FROM full_date)::INTEGER            AS week_of_year,
        (EXTRACT(DOW FROM full_date) IN (0, 6))         AS is_weekend,

        -- Academic calendar (North America: Fall=Sep-Dec, Spring=Jan-Apr, Summer=May-Aug)
        CASE
            WHEN EXTRACT(MONTH FROM full_date) BETWEEN 9  AND 12 THEN
                'Fall '    || EXTRACT(YEAR FROM full_date)::VARCHAR
            WHEN EXTRACT(MONTH FROM full_date) BETWEEN 1  AND 4  THEN
                'Spring '  || EXTRACT(YEAR FROM full_date)::VARCHAR
            ELSE
                'Summer '  || EXTRACT(YEAR FROM full_date)::VARCHAR
        END AS academic_semester,

        -- US Federal fiscal year: Oct 1 - Sep 30
        CASE
            WHEN EXTRACT(MONTH FROM full_date) >= 10
            THEN EXTRACT(YEAR FROM full_date)::INTEGER + 1
            ELSE EXTRACT(YEAR FROM full_date)::INTEGER
        END AS us_fiscal_year,

        -- Canadian fiscal year: Apr 1 - Mar 31
        CASE
            WHEN EXTRACT(MONTH FROM full_date) >= 4
            THEN EXTRACT(YEAR FROM full_date)::INTEGER + 1
            ELSE EXTRACT(YEAR FROM full_date)::INTEGER
        END AS ca_fiscal_year

    FROM date_spine
)

SELECT * FROM date_attributes
ORDER BY full_date
```

---

## Step 2.4 — Staging: `stg_nsf_awards`

Staging is the **trust boundary**. This is the only model that touches raw JSON. If NSF renames a field, you fix it here and everything downstream is unaffected.

Create `models/staging/stg_nsf_awards.sql`:

```sql
-- models/staging/stg_nsf_awards.sql
-- Parses raw_nsf_awards JSON into typed, clean columns.
-- Input:  raw_nsf_awards (raw JSON, never modified)
-- Output: one row per award, all fields typed correctly
--
-- This is a VIEW (not a table) — it recomputes on every query.
-- If the raw data grows, the view always reflects the latest.

WITH raw AS (
    SELECT
        award_id,
        raw_json,
        extracted_at,
        row_hash
    FROM main.raw_nsf_awards   -- main schema = DuckDB default
),

parsed AS (
    SELECT
        award_id,

        -- Title: clean whitespace
        TRIM(raw_json->>'title')                                    AS title,

        -- Agency
        COALESCE(raw_json->>'agency', 'NSF')                       AS agency,

        -- Amount: string → decimal
        TRY_CAST(
            REPLACE(COALESCE(raw_json->>'fundsObligatedAmt', ''), ',', '')
            AS DECIMAL(14, 2)
        )                                                           AS amount_usd,

        -- Dates: 'MM/DD/YYYY' → DATE
        TRY_STRPTIME(raw_json->>'startDate', '%m/%d/%Y')::DATE     AS start_date,
        TRY_STRPTIME(raw_json->>'expDate',   '%m/%d/%Y')::DATE     AS end_date,
        TRY_STRPTIME(raw_json->>'date',      '%m/%d/%Y')::DATE     AS award_date,

        -- Principal Investigator
        NULLIF(TRIM(
            COALESCE(raw_json->>'piFirstName', '') || ' ' ||
            COALESCE(raw_json->>'piLastName',  '')
        ), '')                                                      AS pi_name,
        NULLIF(raw_json->>'piEmail', '')                           AS pi_email,

        -- Institution (nested object)
        NULLIF(raw_json->'awardeeOrganization'->>'name', '')       AS institution_name,
        NULLIF(raw_json->'awardeeOrganization'->>'city', '')       AS institution_city,
        NULLIF(
            COALESCE(
                raw_json->'awardeeOrganization'->>'stateName',
                raw_json->'awardeeOrganization'->>'stateCode'
            ), ''
        )                                                           AS institution_state,

        -- Program and classification
        NULLIF(raw_json->>'primaryProgram', '')                    AS program_name,
        NULLIF(raw_json->>'transType', '')                         AS transaction_type,  -- 'Grant', 'Contract'
        NULLIF(raw_json->>'keyword', '')                           AS keywords,
        NULLIF(raw_json->>'abstractText', '')                      AS abstract,

        -- Derive: grant duration in months
        CASE
            WHEN TRY_STRPTIME(raw_json->>'startDate', '%m/%d/%Y') IS NOT NULL
             AND TRY_STRPTIME(raw_json->>'expDate',   '%m/%d/%Y') IS NOT NULL
            THEN DATEDIFF(
                'month',
                TRY_STRPTIME(raw_json->>'startDate', '%m/%d/%Y')::DATE,
                TRY_STRPTIME(raw_json->>'expDate',   '%m/%d/%Y')::DATE
            )
        END                                                         AS duration_months,

        -- Derive: is this grant currently active?
        CASE
            WHEN TRY_STRPTIME(raw_json->>'expDate', '%m/%d/%Y')::DATE >= CURRENT_DATE
            THEN TRUE
            ELSE FALSE
        END                                                         AS is_active,

        -- Data quality: count non-null required fields
        (
            (CASE WHEN raw_json->>'fundsObligatedAmt' IS NOT NULL AND raw_json->>'fundsObligatedAmt' != '' THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->>'startDate' IS NOT NULL AND raw_json->>'startDate' != '' THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->>'expDate' IS NOT NULL AND raw_json->>'expDate' != '' THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->>'piFirstName' IS NOT NULL AND raw_json->>'piFirstName' != '' THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->>'primaryProgram' IS NOT NULL AND raw_json->>'primaryProgram' != '' THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->'awardeeOrganization'->>'name' IS NOT NULL THEN 1 ELSE 0 END) +
            (CASE WHEN raw_json->>'abstractText' IS NOT NULL AND raw_json->>'abstractText' != '' THEN 1 ELSE 0 END)
        ) / 7.0                                                     AS data_quality_score,

        extracted_at,
        row_hash

    FROM raw
)

SELECT * FROM parsed
-- Filter out records with no award_id (malformed)
WHERE award_id IS NOT NULL
```

---

## Step 2.5 — Intermediate: `int_field_taxonomy_map`

This model maps NSF program names to CIP codes and broad categories. It's the entity resolution layer that makes cross-source analytics possible.

Create `models/intermediate/int_nsf_field_mapped.sql`:

```sql
-- models/intermediate/int_nsf_field_mapped.sql
-- Maps NSF program codes → CIP taxonomy → broad field categories.
-- This is the hardest model to get right because NSF program names
-- are human-readable strings like "CISE/Division of Computing and Networks"
-- and CIP codes are structured like "11.07".
--
-- Strategy: keyword matching on program_name → CIP parent code → broad category.
-- Not perfect, but 80% coverage is enough for trend analysis.

WITH nsf AS (
    SELECT * FROM {{ ref('stg_nsf_awards') }}
    WHERE program_name IS NOT NULL
),

field_mapping AS (
    SELECT
        award_id,
        title,
        amount_usd,
        start_date,
        end_date,
        is_active,
        duration_months,
        pi_name,
        institution_name,
        institution_state,
        program_name,
        abstract,
        data_quality_score,
        extracted_at,

        -- Map NSF program names to CIP parent codes using keyword matching
        -- This is approximate — improve iteratively as you see real data
        CASE
            WHEN LOWER(program_name) LIKE '%comput%'
              OR LOWER(program_name) LIKE '%cise%'
              OR LOWER(program_name) LIKE '%software%'
              OR LOWER(program_name) LIKE '%information science%'
            THEN '11'   -- Computer Science

            WHEN LOWER(program_name) LIKE '%engineer%'
              OR LOWER(program_name) LIKE '%ise%'    -- Engineering Directorate
            THEN '14'   -- Engineering

            WHEN LOWER(program_name) LIKE '%bio%'
              OR LOWER(program_name) LIKE '%life science%'
              OR LOWER(program_name) LIKE '%ecology%'
              OR LOWER(program_name) LIKE '%molecular%'
            THEN '26'   -- Biological Sciences

            WHEN LOWER(program_name) LIKE '%math%'
              OR LOWER(program_name) LIKE '%statistic%'
              OR LOWER(program_name) LIKE '%probability%'
            THEN '27'   -- Mathematics

            WHEN LOWER(program_name) LIKE '%physic%'
              OR LOWER(program_name) LIKE '%chem%'
              OR LOWER(program_name) LIKE '%astro%'
              OR LOWER(program_name) LIKE '%material%'
            THEN '40'   -- Physical Sciences

            WHEN LOWER(program_name) LIKE '%psycholog%'
            THEN '42'

            WHEN LOWER(program_name) LIKE '%social science%'
              OR LOWER(program_name) LIKE '%econom%'
              OR LOWER(program_name) LIKE '%sociolog%'
              OR LOWER(program_name) LIKE '%political%'
            THEN '45'   -- Social Sciences

            WHEN LOWER(program_name) LIKE '%health%'
              OR LOWER(program_name) LIKE '%medic%'
              OR LOWER(program_name) LIKE '%clinical%'
            THEN '51'   -- Health

            ELSE 'XX'   -- Unknown / unmapped
        END AS cip_parent_code,

        -- Map to broad category (for high-level dashboard views)
        CASE
            WHEN LOWER(program_name) LIKE ANY ('%comput%', '%cise%', '%engineer%',
                '%bio%', '%math%', '%physic%', '%chem%', '%material%', '%astro%',
                '%ise%', '%stem%')
            THEN 'STEM'
            WHEN LOWER(program_name) LIKE ANY ('%health%', '%medic%', '%clinical%', '%nursing%')
            THEN 'Health'
            WHEN LOWER(program_name) LIKE ANY ('%social%', '%econom%', '%psychol%', '%political%')
            THEN 'Social Sciences'
            WHEN LOWER(program_name) LIKE ANY ('%humanity%', '%humanities%', '%histor%', '%literatur%', '%linguist%')
            THEN 'Humanities'
            ELSE 'Other'
        END AS broad_category,

        'nsf' AS source_agency

    FROM nsf
)

SELECT * FROM field_mapping
```

---

## Step 2.6 — Mart: `mart_funding_by_field`

This mart answers BQ-4 and BQ-5 directly. Every row is a pre-aggregated summary — no computation at query time.

Create `models/marts/mart_funding_by_field.sql`:

```sql
-- models/marts/mart_funding_by_field.sql
-- Answers: "Which fields have the most funding? How is it trending?"
-- Pre-aggregated so dashboard queries run in milliseconds.
--
-- One row per: (broad_category, cip_parent_code, source_agency, year, quarter)
-- This granularity supports both high-level (by year) and detailed (by quarter) views.

WITH mapped AS (
    SELECT * FROM {{ ref('int_nsf_field_mapped') }}
    WHERE amount_usd IS NOT NULL
      AND amount_usd > 0
      AND start_date IS NOT NULL
),

aggregated AS (
    SELECT
        broad_category,
        cip_parent_code,
        source_agency,

        -- Time dimensions (from the grant's START date, not award date)
        EXTRACT(YEAR FROM start_date)::INTEGER    AS year,
        EXTRACT(QUARTER FROM start_date)::INTEGER AS quarter,

        -- Funding measures
        COUNT(*)                                  AS opportunity_count,
        SUM(amount_usd)                           AS total_funding_usd,
        AVG(amount_usd)                           AS avg_award_usd,
        MEDIAN(amount_usd)                        AS median_award_usd,
        MAX(amount_usd)                           AS max_award_usd,
        MIN(amount_usd)                           AS min_award_usd,

        -- Active grants (those whose end_date is in the future)
        COUNT(*) FILTER (WHERE is_active = TRUE)  AS active_count,
        SUM(amount_usd) FILTER (WHERE is_active = TRUE) AS active_funding_usd,

        -- Data quality
        AVG(data_quality_score)                   AS avg_data_quality,
        COUNT(*) FILTER (WHERE data_quality_score >= 0.8) AS high_quality_count,

        -- Metadata
        MAX(extracted_at)                         AS last_updated_at

    FROM mapped
    WHERE year BETWEEN 2010 AND EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER + 1
      AND broad_category != 'Other'   -- Exclude unmapped records
    GROUP BY 1, 2, 3, 4, 5
)

SELECT * FROM aggregated
ORDER BY year DESC, total_funding_usd DESC
```

---

## Step 2.7 — Mart: `mart_source_health`

This mart powers the Pipeline Health dashboard. It's your proof that you build observable systems.

Create `models/marts/mart_source_health.sql`:

```sql
-- models/marts/mart_source_health.sql
-- Answers: "Is our pipeline healthy? Which sources have quality issues?"
-- One row per: (source_name, year, month)

SELECT
    source_name,
    source_tier,
    status,
    EXTRACT(YEAR  FROM crawled_at)::INTEGER AS year,
    EXTRACT(MONTH FROM crawled_at)::INTEGER AS month,

    COUNT(*)                                        AS total_crawls,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_crawls,
    SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) AS failed_crawls,
    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial_crawls,

    ROUND(
        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0),
        1
    )                                               AS success_rate_pct,

    SUM(records_loaded)                             AS total_records_loaded,
    AVG(quality_avg)                                AS avg_data_quality,
    AVG(duration_secs)                              AS avg_duration_secs,
    MAX(crawled_at)                                 AS last_crawled_at,

    -- Alert: has this source been crawled in the last 48 hours?
    CASE
        WHEN MAX(crawled_at) >= (CURRENT_TIMESTAMP - INTERVAL '48 hours')
        THEN TRUE ELSE FALSE
    END AS crawled_recently

FROM main.raw_crawl_log
GROUP BY 1, 2, 3, 4, 5
ORDER BY year DESC, month DESC, source_name
```

---

## Step 2.8 — dbt Tests (Data Quality)

Create `models/staging/schema.yml`:

```yaml
# models/staging/schema.yml
# dbt tests run with: dbt test
# Failed tests don't stop the pipeline but are logged as warnings/errors.

version: 2

models:
  - name: stg_nsf_awards
    description: "Parsed NSF Award Search API data. One row per award."
    columns:
      - name: award_id
        description: "NSF award ID (e.g. '2401234')"
        tests:
          - not_null
          - unique

      - name: amount_usd
        description: "Award amount in USD. Null if NSF didn't provide it."
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 100000000    # $100M max is reasonable
              inclusive: true
              where: "amount_usd is not null"

      - name: data_quality_score
        tests:
          - dbt_utils.accepted_range:
              min_value: 0.0
              max_value: 1.0
              inclusive: true

  - name: mart_funding_by_field
    description: "Pre-aggregated funding by field and year. Feeds main dashboard."
    columns:
      - name: broad_category
        tests:
          - not_null
          - accepted_values:
              values: ['STEM', 'Health', 'Social Sciences', 'Humanities', 'Other']

      - name: total_funding_usd
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: 0
```

Install dbt-utils:
```bash
# In transform/scholarhub/packages.yml
cat > packages.yml << 'EOF'
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.0.0", "<2.0.0"]
EOF

dbt deps
```

---

## Step 2.9 — Run the Full dbt Pipeline

```bash
cd transform/scholarhub

# Run all models in dependency order
dbt run

# Expected output:
# 1 of 5 START sql view model staging.stg_nsf_awards ................ [RUN]
# 1 of 5 OK created sql view model staging.stg_nsf_awards ........... [OK]
# 2 of 5 START sql table model intermediate.int_nsf_field_mapped .... [RUN]
# ...
# 5 of 5 OK created sql table model marts.mart_funding_by_field ..... [OK]
# Finished running 5 models in 2.34s.

# Run tests
dbt test

# Generate documentation
dbt docs generate
dbt docs serve   # Opens browser at http://localhost:8080
```

Verify marts in DuckDB:
```bash
python -c "
import duckdb
conn = duckdb.connect('warehouse/scholarhub.duckdb')

# Check mart was created
result = conn.execute('''
    SELECT broad_category, year, opportunity_count, ROUND(total_funding_usd/1e6, 1) AS total_million_usd
    FROM analytics_marts.mart_funding_by_field
    WHERE year >= 2020
    ORDER BY year DESC, total_funding_usd DESC
    LIMIT 10
''').fetchdf()
print(result.to_string())
"
```

---

## Step 2.10 — Add `mart_deadline_calendar`

This mart answers "what month has the most opportunities?" (BQ-4 — seasonality).

Create `models/marts/mart_deadline_calendar.sql`:

```sql
-- models/marts/mart_deadline_calendar.sql
-- Answers: "When should students be looking for opportunities?"
-- Groups awards by their start month to show seasonality.

WITH nsf_with_month AS (
    SELECT
        broad_category,
        cip_parent_code,
        EXTRACT(MONTH FROM start_date)::INTEGER  AS start_month,
        EXTRACT(YEAR  FROM start_date)::INTEGER  AS start_year,
        amount_usd,
        is_active,
        source_agency
    FROM {{ ref('int_nsf_field_mapped') }}
    WHERE start_date IS NOT NULL
),

monthly_agg AS (
    SELECT
        broad_category,
        cip_parent_code,
        start_month,
        source_agency,

        -- Month name for display
        CASE start_month
            WHEN 1  THEN 'January'   WHEN 2  THEN 'February'
            WHEN 3  THEN 'March'     WHEN 4  THEN 'April'
            WHEN 5  THEN 'May'       WHEN 6  THEN 'June'
            WHEN 7  THEN 'July'      WHEN 8  THEN 'August'
            WHEN 9  THEN 'September' WHEN 10 THEN 'October'
            WHEN 11 THEN 'November'  WHEN 12 THEN 'December'
        END AS month_name,

        -- Academic season
        CASE
            WHEN start_month BETWEEN 9  AND 12 THEN 'Fall'
            WHEN start_month BETWEEN 1  AND 4  THEN 'Spring'
            ELSE 'Summer'
        END AS academic_season,

        COUNT(*)            AS opportunity_count,
        SUM(amount_usd)     AS total_funding_usd,
        AVG(amount_usd)     AS avg_award_usd

    FROM nsf_with_month
    GROUP BY 1, 2, 3, 4, 5, 6, 7
)

SELECT * FROM monthly_agg
ORDER BY start_month, broad_category
```

---

## Phase 2 Checklist

Before moving to Phase 3, verify:

- [ ] `dbt debug` — "All checks passed!"
- [ ] `dbt run` — all 5+ models build successfully
- [ ] `dbt test` — all tests pass (or you understand why they fail)
- [ ] `mart_funding_by_field` has rows with real dollar amounts
- [ ] `mart_source_health` shows your crawl run from Phase 1
- [ ] `mart_deadline_calendar` shows monthly distribution
- [ ] `dbt docs serve` opens and shows lineage graph

**What you've built:**
- Full ELT pipeline: raw JSON → staging views → intermediate tables → mart tables
- Data quality tests that catch schema breaks automatically
- A model that maps NSF program names to CIP taxonomy (entity resolution)
- Pre-aggregated marts that answer BQ-4 and BQ-5 in milliseconds
- Auto-generated data documentation

**Next:** Phase 3 — Add Canadian sources (NSERC + CIHR) to achieve full North America coverage.
