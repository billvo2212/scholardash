-- models/marts/mart_funding_by_year.sql
/*
Mart: Funding by Year

Provides yearly summary statistics for overall NSF funding trends.
Useful for high-level dashboards and executive summaries.

Materialized as TABLE for fast querying.
*/

{{ config(
    materialized='table'
) }}

WITH awards AS (
    SELECT *
    FROM {{ ref('int_all_awards') }}
    WHERE award_year IS NOT NULL
),

yearly_summary AS (
    SELECT
        award_year,

        -- Award metrics
        COUNT(*) AS total_awards,
        SUM(funding_amount) AS total_funding,
        AVG(funding_amount) AS avg_award_size,
        MEDIAN(funding_amount) AS median_award_size,
        MIN(funding_amount) AS min_award_size,
        MAX(funding_amount) AS max_award_size,

        -- Source breakdown
        COUNT(CASE WHEN source = 'NSF' THEN 1 END) AS nsf_awards,
        COUNT(CASE WHEN source = 'NIH' THEN 1 END) AS nih_awards,
        SUM(CASE WHEN source = 'NSF' THEN funding_amount ELSE 0 END) AS nsf_funding,
        SUM(CASE WHEN source = 'NIH' THEN funding_amount ELSE 0 END) AS nih_funding,

        -- Diversity metrics
        COUNT(DISTINCT pi_name) AS distinct_pis,
        COUNT(DISTINCT institution) AS distinct_institutions,
        COUNT(DISTINCT state) AS distinct_states,
        COUNT(DISTINCT directorate) AS distinct_directorates,
        COUNT(DISTINCT program_name) AS distinct_programs,

        -- Quality metrics
        AVG(quality_score) AS avg_quality_score,
        AVG(duration_months) AS avg_duration_months

    FROM awards
    GROUP BY award_year
),

with_growth AS (
    SELECT
        *,

        -- Year-over-year growth calculations
        LAG(total_funding, 1) OVER (ORDER BY award_year) AS prev_year_funding,
        LAG(total_awards, 1) OVER (ORDER BY award_year) AS prev_year_awards,

        -- Growth percentages
        CASE
            WHEN LAG(total_funding, 1) OVER (ORDER BY award_year) IS NOT NULL
                AND LAG(total_funding, 1) OVER (ORDER BY award_year) > 0
            THEN ROUND(
                100.0 * (total_funding - LAG(total_funding, 1) OVER (ORDER BY award_year))
                    / LAG(total_funding, 1) OVER (ORDER BY award_year),
                2
            )
            ELSE NULL
        END AS funding_growth_pct,

        CASE
            WHEN LAG(total_awards, 1) OVER (ORDER BY award_year) IS NOT NULL
                AND LAG(total_awards, 1) OVER (ORDER BY award_year) > 0
            THEN ROUND(
                100.0 * (total_awards - LAG(total_awards, 1) OVER (ORDER BY award_year))
                    / LAG(total_awards, 1) OVER (ORDER BY award_year),
                2
            )
            ELSE NULL
        END AS award_count_growth_pct

    FROM yearly_summary
)

SELECT * FROM with_growth
ORDER BY award_year DESC
