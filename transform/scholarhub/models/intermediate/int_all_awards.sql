-- models/intermediate/int_all_awards.sql
/*
Intermediate: Unified Awards from All Sources

Combines NSF and NIH data into a common schema for unified analysis.
This enables cross-source comparisons and consolidated reporting.

Materialized as TABLE for performance (used by multiple marts).
*/

{{ config(
    materialized='table',
    indexes=[
        {'columns': ['source'], 'unique': false},
        {'columns': ['award_year'], 'unique': false},
        {'columns': ['institution'], 'unique': false}
    ]
) }}

WITH nsf_awards AS (
    SELECT
        'NSF' AS source,
        award_id AS award_id,
        title,
        abstract,
        pi_name,
        pi_email,
        institution,
        city,
        state,
        state_code AS state_code,
        country,
        funding_amount,
        start_date,
        end_date,
        award_year,
        duration_months,
        program_name,
        directorate,
        division,
        quality_score,
        is_valid_record,
        extracted_at
    FROM {{ ref('stg_nsf_awards') }}
    WHERE is_valid_record = TRUE
),

nih_projects AS (
    SELECT
        'NIH' AS source,
        project_num AS award_id,
        title,
        abstract,
        pi_name,
        NULL AS pi_email,  -- NIH doesn't provide PI email
        organization AS institution,
        institution_city AS city,
        institution_state AS state,
        institution_state AS state_code,  -- NIH doesn't have separate state code
        institution_country AS country,
        award_amount AS funding_amount,
        start_date,
        end_date,
        award_year,
        duration_months,
        CONCAT(institute_abbr, ' ', activity_code) AS program_name,
        institute_abbr AS directorate,
        funding_institute_name AS division,
        quality_score,
        is_valid_record,
        extracted_at
    FROM {{ ref('stg_nih_projects') }}
    WHERE is_valid_record = TRUE
),

unified AS (
    SELECT * FROM nsf_awards
    UNION ALL
    SELECT * FROM nih_projects
)

SELECT
    *,
    ROW_NUMBER() OVER (ORDER BY extracted_at, source, award_id) AS unified_award_id
FROM unified
