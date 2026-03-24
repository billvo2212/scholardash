-- models/marts/mart_funding_by_field.sql
/*
Mart: Funding by Field

Answers: BQ-2: Which fields are growing or shrinking in funding over time?

Aggregates funding by program/directorate and year to show trends.
Pre-computed for dashboard time-series analysis.

Materialized as TABLE for fast querying.
*/

{{ config(
    materialized='table',
    indexes=[
        {'columns': ['directorate'], 'unique': false},
        {'columns': ['award_year'], 'unique': false}
    ]
) }}

WITH awards AS (
    SELECT *
    FROM {{ ref('int_all_awards') }}
    WHERE award_year IS NOT NULL
),

by_field AS (
    SELECT
        directorate,
        division,
        program_name,
        award_year,

        -- Funding metrics
        COUNT(*) AS total_awards,
        SUM(funding_amount) AS total_funding,
        AVG(funding_amount) AS avg_award_size,

        -- PI metrics
        COUNT(DISTINCT pi_name) AS distinct_pis,

        -- Institution metrics
        COUNT(DISTINCT institution) AS distinct_institutions

    FROM awards
    GROUP BY directorate, division, program_name, award_year
),

with_trends AS (
    SELECT
        *,

        -- Calculate year-over-year growth
        LAG(total_funding, 1) OVER (
            PARTITION BY directorate, division, program_name
            ORDER BY award_year
        ) AS prev_year_funding,

        -- Calculate percentage change
        CASE
            WHEN LAG(total_funding, 1) OVER (
                PARTITION BY directorate, division, program_name
                ORDER BY award_year
            ) IS NOT NULL AND LAG(total_funding, 1) OVER (
                PARTITION BY directorate, division, program_name
                ORDER BY award_year
            ) > 0
            THEN ROUND(
                100.0 * (total_funding - LAG(total_funding, 1) OVER (
                    PARTITION BY directorate, division, program_name
                    ORDER BY award_year
                )) / LAG(total_funding, 1) OVER (
                    PARTITION BY directorate, division, program_name
                    ORDER BY award_year
                ),
                2
            )
            ELSE NULL
        END AS yoy_growth_pct

    FROM by_field
)

SELECT * FROM with_trends
ORDER BY directorate, division, program_name, award_year
