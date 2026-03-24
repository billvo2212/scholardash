-- models/staging/stg_nsf_awards.sql
/*
Staging model for NSF Awards.

Parses JSON from raw_nsf_awards into typed columns.
Quality: Preserves raw data quality score, adds validation flags.

Inputs: raw_nsf_awards (main schema)
Outputs: View in staging schema

Note: This is a VIEW (cheap, no storage). It re-parses JSON on every query.
For 500 records this is fine. At 500K records, materialize as table.
*/

WITH source AS (
    SELECT *
    FROM main.raw_nsf_awards
),

parsed AS (
    SELECT
        id,
        extracted_at,
        quality_score,

        -- Parse JSON fields
        TRY_CAST(json_extract_string(response_json, '$.id') AS VARCHAR) AS award_id,
        TRY_CAST(json_extract_string(response_json, '$.title') AS VARCHAR) AS title,
        TRY_CAST(json_extract_string(response_json, '$.abstractText') AS VARCHAR) AS abstract,

        -- Principal Investigator
        TRY_CAST(json_extract_string(response_json, '$.piFirstName') AS VARCHAR) AS pi_first_name,
        TRY_CAST(json_extract_string(response_json, '$.piLastName') AS VARCHAR) AS pi_last_name,
        TRY_CAST(json_extract_string(response_json, '$.pdPIName') AS VARCHAR) AS pi_full_name,
        TRY_CAST(json_extract_string(response_json, '$.piEmail') AS VARCHAR) AS pi_email,

        -- Institution / Location
        TRY_CAST(json_extract_string(response_json, '$.perfLocation') AS VARCHAR) AS institution,
        TRY_CAST(json_extract_string(response_json, '$.perfCity') AS VARCHAR) AS city,
        TRY_CAST(json_extract_string(response_json, '$.perfState') AS VARCHAR) AS state,
        TRY_CAST(json_extract_string(response_json, '$.perfStateCode') AS VARCHAR) AS state_code,
        TRY_CAST(json_extract_string(response_json, '$.perfCountry') AS VARCHAR) AS country,
        TRY_CAST(json_extract_string(response_json, '$.perfCountryCode') AS VARCHAR) AS country_code,

        -- Funding
        TRY_CAST(json_extract_string(response_json, '$.fundsObligatedAmt') AS DECIMAL(12,2)) AS funding_amount,
        TRY_CAST(json_extract_string(response_json, '$.estimatedTotalAmt') AS DECIMAL(12,2)) AS estimated_total_amount,

        -- Dates (MM/DD/YYYY format in NSF API)
        TRY_CAST(strptime(json_extract_string(response_json, '$.startDate'), '%m/%d/%Y') AS DATE) AS start_date,
        TRY_CAST(strptime(json_extract_string(response_json, '$.expDate'), '%m/%d/%Y') AS DATE) AS end_date,

        -- Program Information
        TRY_CAST(json_extract_string(response_json, '$.fundProgramName') AS VARCHAR) AS program_name,
        TRY_CAST(json_extract_string(response_json, '$.primaryProgram') AS VARCHAR) AS primary_program,
        TRY_CAST(json_extract_string(response_json, '$.dirAbbr') AS VARCHAR) AS directorate,
        TRY_CAST(json_extract_string(response_json, '$.divAbbr') AS VARCHAR) AS division,

        -- Award Type
        TRY_CAST(json_extract_string(response_json, '$.transType') AS VARCHAR) AS transaction_type,
        TRY_CAST(json_extract_string(response_json, '$.agency') AS VARCHAR) AS agency,

        -- Full JSON for reference
        response_json

    FROM source
),

validated AS (
    SELECT
        *,

        -- Validation flags (for data quality monitoring)
        CASE
            WHEN award_id IS NULL THEN FALSE
            WHEN title IS NULL OR LENGTH(TRIM(title)) = 0 THEN FALSE
            WHEN funding_amount IS NULL OR funding_amount <= 0 THEN FALSE
            ELSE TRUE
        END AS is_valid_record,

        -- Derived fields
        CASE
            WHEN pi_first_name IS NOT NULL AND pi_last_name IS NOT NULL
                THEN pi_first_name || ' ' || pi_last_name
            ELSE pi_full_name
        END AS pi_name,

        -- Calculate award duration in months
        CASE
            WHEN start_date IS NOT NULL AND end_date IS NOT NULL
                THEN DATEDIFF('month', start_date, end_date)
            ELSE NULL
        END AS duration_months,

        -- Extract year from start date
        CASE
            WHEN start_date IS NOT NULL
                THEN EXTRACT(YEAR FROM start_date)
            ELSE NULL
        END AS award_year

    FROM parsed
)

SELECT * FROM validated
