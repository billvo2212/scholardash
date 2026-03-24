-- models/marts/mart_funding_by_institution.sql
/*
Mart: Funding by Institution

Answers: BQ-5: Which institutions have the most active funded capacity?

Aggregates total funding, award counts, and average award size by institution.
Pre-computed for dashboard queries.

Materialized as TABLE for fast querying.
*/

{{ config(
    materialized='table',
    indexes=[{'columns': ['institution'], 'unique': false}]
) }}

WITH awards AS (
    SELECT *
    FROM {{ ref('int_all_awards') }}
),

aggregated AS (
    SELECT
        institution,
        state,
        state_code,
        country,

        -- Funding metrics
        COUNT(*) AS total_awards,
        SUM(funding_amount) AS total_funding,
        AVG(funding_amount) AS avg_award_size,
        MIN(funding_amount) AS min_award_size,
        MAX(funding_amount) AS max_award_size,

        -- Source breakdown
        COUNT(CASE WHEN source = 'NSF' THEN 1 END) AS nsf_awards,
        COUNT(CASE WHEN source = 'NIH' THEN 1 END) AS nih_awards,
        SUM(CASE WHEN source = 'NSF' THEN funding_amount ELSE 0 END) AS nsf_funding,
        SUM(CASE WHEN source = 'NIH' THEN funding_amount ELSE 0 END) AS nih_funding,

        -- Time metrics
        MIN(start_date) AS first_award_date,
        MAX(start_date) AS most_recent_award_date,
        COUNT(DISTINCT award_year) AS years_active,

        -- Quality metrics
        AVG(quality_score) AS avg_quality_score,

        -- Program diversity
        COUNT(DISTINCT program_name) AS distinct_programs,
        COUNT(DISTINCT directorate) AS distinct_directorates

    FROM awards
    GROUP BY institution, state, state_code, country
),

ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (ORDER BY total_funding DESC) AS funding_rank,
        ROW_NUMBER() OVER (ORDER BY total_awards DESC) AS award_count_rank,

        -- Calculate percentage of total funding
        ROUND(
            100.0 * total_funding / SUM(total_funding) OVER (),
            2
        ) AS pct_of_total_funding

    FROM aggregated
)

SELECT * FROM ranked
ORDER BY total_funding DESC
