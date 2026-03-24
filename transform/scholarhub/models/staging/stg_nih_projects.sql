-- models/staging/stg_nih_projects.sql
/*
Staging model for NIH Projects.

Parses JSON from raw_nih_projects into typed columns.
Quality: Preserves raw data quality score, adds validation flags.

Inputs: raw_nih_projects (main schema)
Outputs: View in staging schema

Note: This is a VIEW (cheap, no storage).
*/

WITH source AS (
    SELECT *
    FROM main.raw_nih_projects
),

parsed AS (
    SELECT
        id,
        extracted_at,
        quality_score,

        -- Parse JSON fields
        TRY_CAST(json_extract_string(response_json, '$.project_num') AS VARCHAR) AS project_num,
        TRY_CAST(json_extract_string(response_json, '$.project_title') AS VARCHAR) AS title,
        TRY_CAST(json_extract_string(response_json, '$.abstract_text') AS VARCHAR) AS abstract,

        -- Principal Investigator
        TRY_CAST(json_extract_string(response_json, '$.contact_pi_name') AS VARCHAR) AS pi_name,
        TRY_CAST(json_extract_string(response_json, '$.pi_profile_id') AS VARCHAR) AS pi_profile_id,

        -- Organization
        TRY_CAST(json_extract_string(response_json, '$.organization.org_name') AS VARCHAR) AS organization,
        TRY_CAST(json_extract_string(response_json, '$.organization.city') AS VARCHAR) AS city,
        TRY_CAST(json_extract_string(response_json, '$.organization.state') AS VARCHAR) AS state,
        TRY_CAST(json_extract_string(response_json, '$.organization.country') AS VARCHAR) AS country,
        TRY_CAST(json_extract_string(response_json, '$.organization.org_city') AS VARCHAR) AS org_city,
        TRY_CAST(json_extract_string(response_json, '$.organization.org_state') AS VARCHAR) AS org_state,
        TRY_CAST(json_extract_string(response_json, '$.organization.org_country') AS VARCHAR) AS org_country,

        -- Funding
        TRY_CAST(json_extract_string(response_json, '$.award_amount') AS DECIMAL(12,2)) AS award_amount,
        TRY_CAST(json_extract_string(response_json, '$.fiscal_year') AS INTEGER) AS fiscal_year,

        -- Dates
        TRY_CAST(json_extract_string(response_json, '$.project_start_date') AS DATE) AS start_date,
        TRY_CAST(json_extract_string(response_json, '$.project_end_date') AS DATE) AS end_date,
        TRY_CAST(json_extract_string(response_json, '$.award_notice_date') AS DATE) AS award_notice_date,

        -- Program Information
        TRY_CAST(json_extract_string(response_json, '$.activity_code') AS VARCHAR) AS activity_code,
        TRY_CAST(json_extract_string(response_json, '$.agency_ic_admin.name') AS VARCHAR) AS institute_name,
        TRY_CAST(json_extract_string(response_json, '$.agency_ic_admin.abbreviation') AS VARCHAR) AS institute_abbr,
        TRY_CAST(json_extract_string(response_json, '$.agency_ic_fundings[0].name') AS VARCHAR) AS funding_institute_name,

        -- Award Type
        TRY_CAST(json_extract_string(response_json, '$.award_type') AS VARCHAR) AS award_type,
        TRY_CAST(json_extract_string(response_json, '$.subproject_id') AS VARCHAR) AS subproject_id,

        -- Full JSON for reference
        response_json

    FROM source
),

validated AS (
    SELECT
        *,

        -- Validation flags
        CASE
            WHEN project_num IS NULL THEN FALSE
            WHEN title IS NULL OR LENGTH(TRIM(title)) = 0 THEN FALSE
            WHEN award_amount IS NULL OR award_amount <= 0 THEN FALSE
            ELSE TRUE
        END AS is_valid_record,

        -- Calculate award duration in months
        CASE
            WHEN start_date IS NOT NULL AND end_date IS NOT NULL
                THEN DATEDIFF('month', start_date, end_date)
            ELSE NULL
        END AS duration_months,

        -- Extract year from start date if fiscal_year is missing
        CASE
            WHEN fiscal_year IS NOT NULL THEN fiscal_year
            WHEN start_date IS NOT NULL THEN EXTRACT(YEAR FROM start_date)
            ELSE NULL
        END AS award_year,

        -- Standardize organization fields (prefer org_* over plain fields)
        COALESCE(org_city, city) AS institution_city,
        COALESCE(org_state, state) AS institution_state,
        COALESCE(org_country, country) AS institution_country

    FROM parsed
)

SELECT * FROM validated
