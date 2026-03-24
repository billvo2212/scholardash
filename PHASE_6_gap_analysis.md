# Phase 6 — Funding Gap Analysis

**Goal:** Build the most unique analytical capability in the project — computing the ratio of funded positions to qualified applicants by field. This is BQ-3 and the analytical insight no competitor provides.

**Duration:** ~1 week  
**Prerequisite:** Phases 1–5 complete. Good volume of NSF + NIH + NSERC data.

---

## What Makes This Hard (And Why That's the Point)

The funding gap ratio = funded positions / qualified applicants per field.

Neither number exists in any single database. You have to *construct* them:

```
Funded positions:
  NSF T32 grants    → trainee_count field (explicit)
  NIH T32 grants    → trainee_count in abstract/terms (extract via keyword)
  NSF R01 grants    → estimate: amount_usd / avg_phd_stipend ($35K)
  NSERC CREATE      → HQP target from grant description
  NSERC CGSD/CGSM   → 1 position per award (by definition)

Qualified applicants:
  IPEDS (US)        → PhD enrollment by CIP code
  StatCan Table 37  → Canadian grad enrollment by field
  Estimate method:  enrolled PhD students - known fellowship holders
```

The technical challenge: field taxonomies don't match. NSF uses program codes, IPEDS uses CIP codes, NSERC uses research subjects. The intermediate dbt model `int_field_taxonomy_unified` is the glue.

---

## Step 6.1 — Add IPEDS Data (US Enrollment)

IPEDS doesn't have an API with convenient access, but they publish annual data files.

### Download IPEDS Completions Data

```bash
# Download IPEDS Completions data
# URL: https://nces.ed.gov/ipeds/use-the-data/download-access-database
# File: C{YEAR}_A.csv (Completions by program, level, award type)
# Download the latest year available

mkdir -p data/raw/ipeds
# After downloading manually:
# data/raw/ipeds/IPEDS_C2023_A.csv
```

Create `extractors/institutional/ipeds_extractor.py`:

```python
# extractors/institutional/ipeds_extractor.py
"""
IPEDS Completions Data Extractor.

IPEDS = Integrated Postsecondary Education Data System (US Dept of Education)
Source: https://nces.ed.gov/ipeds/

The Completions survey (table C_A) contains the number of degrees/awards
conferred by CIP code, degree level, and institution.

We use this as a PROXY for "qualified applicants" — the number of students
completing doctoral programs is roughly correlated with how many are applying
for funded PhD positions.

More precise approach (future): use Fall Enrollment data (EF_A) for
enrolled PhD students, not just completions.
"""
import csv
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import time

from extractors.base import BaseExtractor, ExtractResult


# IPEDS degree levels we care about
# 17 = Doctor's degree - research/scholarship (PhD)
# 18 = Doctor's degree - professional practice (MD, JD, etc.)
# 19 = Doctor's degree - other
DOCTORAL_LEVEL_CODES = {'17', '18', '19'}

# Master's level (for MS programs that lead to PhD)
MASTERS_LEVEL_CODES = {'5', '7'}  # Master's degree, Post-master's certificate


class IPEDSExtractor(BaseExtractor):
    """Extracts IPEDS enrollment/completion data for gap analysis."""

    SOURCE_NAME = "ipeds_completions"
    SOURCE_TIER = 2
    RAW_TABLE = "raw_ipeds_completions"

    def extract(self, csv_path: str, survey_year: int = 2023, **kwargs) -> ExtractResult:
        start_time = time.monotonic()
        csv_file = Path(csv_path)

        if not csv_file.exists():
            return ExtractResult(
                source_name=self.SOURCE_NAME,
                records_found=0, records_loaded=0, records_failed=1,
                quality_avg=0.0, duration_secs=0.0,
                errors=[f"File not found: {csv_path}"],
            )

        # Ensure table exists
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_ipeds_completions (
                unitid          VARCHAR,
                cipcode         VARCHAR,
                awlevel         VARCHAR,
                ctotalt         INTEGER,   -- total completions
                survey_year     INTEGER,
                raw_csv_row     JSON,
                extracted_at    TIMESTAMPTZ DEFAULT NOW(),
                row_hash        VARCHAR,
                PRIMARY KEY (unitid, cipcode, awlevel, survey_year)
            )
        """)

        records_found = 0
        records_loaded = 0
        errors = []

        with open(csv_file, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                records_found += 1

                # Only load doctoral and master's level completions
                awlevel = row.get('AWLEVEL', row.get('awlevel', ''))
                if awlevel not in DOCTORAL_LEVEL_CODES | MASTERS_LEVEL_CODES:
                    continue

                try:
                    unitid = row.get('UNITID', row.get('unitid', ''))
                    cipcode = row.get('CIPCODE', row.get('cipcode', ''))
                    ctotalt = int(row.get('CTOTALT', row.get('ctotalt', 0)) or 0)

                    row_json = json.dumps(dict(row))
                    row_hash = hashlib.md5(row_json.encode()).hexdigest()

                    self.conn.execute("""
                        INSERT INTO raw_ipeds_completions
                            (unitid, cipcode, awlevel, ctotalt, survey_year, raw_csv_row, row_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (unitid, cipcode, awlevel, survey_year) DO NOTHING
                    """, [unitid, cipcode, awlevel, ctotalt, survey_year, row_json, row_hash])
                    records_loaded += 1

                except Exception as e:
                    errors.append(str(e))

        duration = time.monotonic() - start_time
        result = ExtractResult(
            source_name=self.SOURCE_NAME,
            records_found=records_found,
            records_loaded=records_loaded,
            records_failed=len(errors),
            quality_avg=0.9,
            duration_secs=round(duration, 2),
            errors=errors[:5],
        )
        self._log_crawl_event(result)
        return result

    def _parse(self, raw_data):
        return raw_data
```

---

## Step 6.2 — Gap Analysis dbt Models

### Intermediate: Funded Positions

Create `models/intermediate/int_funded_positions.sql`:

```sql
-- models/intermediate/int_funded_positions.sql
-- Constructs a "funded position count" from multiple grant sources.
--
-- The challenge: "funded position" doesn't exist as a field.
-- We derive it from:
--   1. T32 grants: explicit trainee slots (highest confidence)
--   2. F31 fellowships: 1 position per award (exact)
--   3. R01 grants: estimate from amount / average PhD stipend + overhead
--   4. NSERC CGSD/CGSM: 1 position per award (exact)
--
-- Confidence levels:
--   HIGH   = T32 trainee count, F31/F32, CGSD/CGSM (direct measurement)
--   MEDIUM = R01 estimate based on amount
--   LOW    = R21/R03 estimate (smaller grants, uncertain student presence)

WITH nih_training AS (
    -- T32: Institutional training grants — explicit PhD slots
    SELECT
        project_num                     AS grant_id,
        'NIH'                           AS agency,
        'US'                            AS country,
        institution_name,
        institution_state               AS region,
        pi_name,
        fiscal_year                     AS year,
        amount_usd,
        activity_code,
        is_active,
        -- T32 explicitly funds trainees; estimate 3-5 per $500K
        -- Actual trainee count is in abstracts (complex to extract)
        -- Using conservative estimate: 1 trainee per $100K
        GREATEST(1, ROUND(COALESCE(amount_usd, 0) / 100000))::INTEGER AS estimated_positions,
        'HIGH'                          AS confidence,
        'training_grant'                AS grant_type,
        '51'                            AS cip_parent  -- NIH = health sciences

    FROM {{ ref('stg_nih_projects') }}
    WHERE is_training_grant = TRUE
      AND amount_usd IS NOT NULL
),

nih_fellowships AS (
    -- F31/F32: Individual fellowships — exactly 1 position per award
    SELECT
        project_num                     AS grant_id,
        'NIH'                           AS agency,
        'US'                            AS country,
        institution_name,
        institution_state               AS region,
        pi_name,
        fiscal_year                     AS year,
        amount_usd,
        activity_code,
        is_active,
        1                               AS estimated_positions,
        'HIGH'                          AS confidence,
        'fellowship'                    AS grant_type,
        '51'                            AS cip_parent

    FROM {{ ref('stg_nih_projects') }}
    WHERE is_fellowship = TRUE
      AND amount_usd IS NOT NULL
),

nsf_training AS (
    -- NSF: Use R-equivalent grants to estimate positions
    -- NSF GRFP and similar fellowships are counted as 1 each
    -- Large grants (>$300K) estimated at 1 position per $100K
    SELECT
        award_id                        AS grant_id,
        'NSF'                           AS agency,
        'US'                            AS country,
        institution_name,
        institution_state               AS region,
        pi_name,
        EXTRACT(YEAR FROM start_date)::INTEGER AS year,
        amount_usd,
        'GRANT'                         AS activity_code,
        is_active,
        CASE
            WHEN amount_usd >= 300000
            THEN GREATEST(1, ROUND(amount_usd / 100000))::INTEGER
            ELSE 1
        END                             AS estimated_positions,
        'MEDIUM'                        AS confidence,
        'research_grant'                AS grant_type,
        cip_parent_code                 AS cip_parent

    FROM {{ ref('int_nsf_field_mapped') }}
    WHERE amount_usd IS NOT NULL
      AND start_date IS NOT NULL
),

nserc_direct AS (
    -- NSERC CGSD/CGSM: Graduate scholarships, exactly 1 position per award
    SELECT
        CAST(row_id AS VARCHAR) || '_' || source_file AS grant_id,
        'NSERC'                         AS agency,
        'Canada'                        AS country,
        institution_name,
        province                        AS region,
        applicant_name                  AS pi_name,
        fiscal_year::INTEGER            AS year,
        amount_usd_approx               AS amount_usd,
        program_code                    AS activity_code,
        NULL                            AS is_active,  -- NSERC CSV lacks end date
        1                               AS estimated_positions,
        'HIGH'                          AS confidence,
        'graduate_award'                AS grant_type,
        'XX_STEM'                       AS cip_parent  -- NSERC is STEM-only

    FROM {{ ref('stg_nserc_awards') }}
    WHERE is_graduate_award = TRUE
      AND amount_cad IS NOT NULL
)

SELECT * FROM nih_training
UNION ALL SELECT * FROM nih_fellowships
UNION ALL SELECT * FROM nsf_training
UNION ALL SELECT * FROM nserc_direct
```

### Intermediate: Qualified Applicants (IPEDS)

Create `models/intermediate/int_qualified_applicants.sql`:

```sql
-- models/intermediate/int_qualified_applicants.sql
-- Constructs "qualified applicant" counts from IPEDS completion data.
--
-- Logic: number of doctoral completions ≈ number of people seeking
-- funded PhD opportunities in that field.
-- This is an approximation — a better proxy would be enrollment data,
-- but completions are more consistently available.

WITH ipeds_doctoral AS (
    SELECT
        cipcode,
        survey_year                         AS year,
        SUM(ctotalt)                        AS doctoral_completions,
        -- CIP parent: first 2 digits
        LEFT(REPLACE(cipcode, '.', ''), 2)  AS cip_parent_raw
    FROM main.raw_ipeds_completions
    WHERE awlevel IN ('17', '18', '19')  -- doctoral levels
      AND ctotalt > 0
    GROUP BY cipcode, survey_year
),

-- Join to taxonomy for broad categories
with_taxonomy AS (
    SELECT
        i.cipcode,
        i.year,
        i.doctoral_completions,
        -- Map to 2-digit parent
        CASE
            WHEN i.cip_parent_raw IN ('11', '14', '26', '27', '40', '41') THEN i.cip_parent_raw
            WHEN i.cip_parent_raw = '51' THEN '51'
            WHEN i.cip_parent_raw IN ('42', '45', '52') THEN i.cip_parent_raw
            ELSE 'XX'
        END                                 AS cip_parent,
        COALESCE(t.broad_category, 'Other') AS broad_category,
        'US'                                AS country
    FROM ipeds_doctoral i
    LEFT JOIN main.seed_cip_taxonomy t
        ON t.cip_code = LEFT(i.cipcode, 2)   -- match on 2-digit parent
),

aggregated AS (
    SELECT
        cip_parent,
        broad_category,
        country,
        year,
        SUM(doctoral_completions) AS qualified_applicants
    FROM with_taxonomy
    GROUP BY 1, 2, 3, 4
)

SELECT * FROM aggregated
WHERE broad_category != 'Other'
ORDER BY year DESC, qualified_applicants DESC
```

### Mart: Funding Gap

Create `models/marts/mart_funding_gap.sql`:

```sql
-- models/marts/mart_funding_gap.sql
-- THE KEY MART: funded positions vs qualified applicants by field.
-- This answers BQ-3 and is the most unique analytical output.
--
-- Gap ratio = funded_positions / qualified_applicants
-- Interpretation:
--   > 20  = Strong supply (student-friendly market)
--   10-20 = Moderate competition
--   5-10  = Competitive
--   < 5   = High competition / critical shortage

WITH positions AS (
    SELECT
        cip_parent,
        agency,
        country,
        year,
        SUM(estimated_positions)    AS funded_positions,
        SUM(amount_usd)             AS total_funding,
        AVG(amount_usd)             AS avg_award,
        COUNT(*)                    AS grant_count,
        SUM(estimated_positions) FILTER (WHERE confidence = 'HIGH') AS high_confidence_positions
    FROM {{ ref('int_funded_positions') }}
    WHERE year BETWEEN 2018 AND EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
    GROUP BY 1, 2, 3, 4
),

applicants AS (
    SELECT
        cip_parent,
        country,
        year,
        SUM(qualified_applicants)   AS qualified_applicants,
        broad_category
    FROM {{ ref('int_qualified_applicants') }}
    GROUP BY 1, 2, 3, 4, 5
),

combined AS (
    SELECT
        COALESCE(p.cip_parent, a.cip_parent)    AS cip_parent,
        COALESCE(p.country, a.country)          AS country,
        COALESCE(p.year, a.year)                AS year,
        COALESCE(a.broad_category, 'STEM')      AS broad_category,
        p.agency,
        COALESCE(p.funded_positions, 0)         AS funded_positions,
        COALESCE(p.high_confidence_positions, 0) AS high_confidence_positions,
        COALESCE(p.total_funding, 0)            AS total_funding,
        COALESCE(p.grant_count, 0)              AS grant_count,
        COALESCE(a.qualified_applicants, 1)     AS qualified_applicants  -- avoid divide by zero

    FROM positions p
    FULL OUTER JOIN applicants a
        ON p.cip_parent = a.cip_parent
       AND p.country = a.country
       AND p.year = a.year
),

with_ratios AS (
    SELECT
        *,
        -- Gap ratio: positions per 100 applicants
        ROUND(
            CAST(funded_positions AS DOUBLE) / NULLIF(qualified_applicants, 0) * 100,
            1
        )                                   AS positions_per_100_applicants,

        -- Confidence-adjusted ratio (use only high-confidence positions)
        ROUND(
            CAST(high_confidence_positions AS DOUBLE) / NULLIF(qualified_applicants, 0) * 100,
            1
        )                                   AS conservative_ratio,

        -- Competition tier
        CASE
            WHEN CAST(funded_positions AS DOUBLE) / NULLIF(qualified_applicants, 0) * 100 >= 20
            THEN 'Strong supply'
            WHEN CAST(funded_positions AS DOUBLE) / NULLIF(qualified_applicants, 0) * 100 >= 10
            THEN 'Moderate'
            WHEN CAST(funded_positions AS DOUBLE) / NULLIF(qualified_applicants, 0) * 100 >= 5
            THEN 'Competitive'
            ELSE 'Critical shortage'
        END                                 AS competition_tier

    FROM combined
    WHERE qualified_applicants > 0
)

SELECT * FROM with_ratios
ORDER BY year DESC, positions_per_100_applicants ASC  -- worst gaps first
```

---

## Step 6.3 — Gap Analysis Dashboard Page

Create `dashboard/pages/6_funding_gap.py`:

```python
# dashboard/pages/6_funding_gap.py
"""
Funding Gap Analysis — BQ-3: Where are PhD students competing hardest?
This is the most unique page in the dashboard — no competitor has this.
"""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import duckdb
import sys
sys.path.insert(0, '.')

from dashboard.utils.data_loader import get_connection

st.title("Funding Gap Analysis")
st.markdown("""
*How many funded positions exist per 100 qualified applicants, by field?*

This analysis joins federal grant data (NSF, NIH, NSERC) with doctoral 
enrollment data (IPEDS) to compute competition ratios no other platform provides.

**Ratio interpretation:**
- 🟢 > 20 — Strong supply: many funded slots relative to applicants
- 🟡 10–20 — Moderate competition
- 🟠 5–10 — Competitive market
- 🔴 < 5 — Critical shortage: very few funded positions
""")


@st.cache_data(ttl=3600)
def load_gap_data(year: int = 2022) -> pd.DataFrame:
    try:
        conn = get_connection()
        return conn.execute(f"""
            SELECT
                broad_category,
                country,
                year,
                SUM(funded_positions)       AS funded_positions,
                SUM(qualified_applicants)   AS qualified_applicants,
                ROUND(
                    SUM(funded_positions)::DOUBLE / NULLIF(SUM(qualified_applicants), 0) * 100,
                    1
                )                           AS positions_per_100,
                MIN(competition_tier)       AS competition_tier
            FROM analytics_marts.mart_funding_gap
            WHERE year = {year}
              AND qualified_applicants > 10
            GROUP BY 1, 2, 3
            ORDER BY positions_per_100 ASC
        """).df()
    except Exception as e:
        st.warning(f"Gap data not yet available: {e}. Run Phase 6 models first.")
        return pd.DataFrame()


# ── Filters ────────────────────────────────────────────────────────────────
with st.sidebar:
    year = st.selectbox("Year", [2022, 2021, 2020, 2019], index=0)
    country = st.selectbox("Country", ["US", "Canada", "Both"], index=0)

df = load_gap_data(year=year)

if df.empty:
    st.info("""
    Funding gap data requires IPEDS enrollment data.
    
    **To enable this page:**
    1. Download IPEDS Completions data from nces.ed.gov/ipeds
    2. Run: `python -c "from extractors.institutional.ipeds_extractor import IPEDSExtractor; IPEDSExtractor().extract('data/raw/ipeds/IPEDS_C2022_A.csv', 2022)"`
    3. Run: `cd transform/scholarhub && dbt run --select mart_funding_gap`
    4. Refresh this page
    """)
    st.stop()

if country != "Both":
    df = df[df['country'] == country]

# ── Summary Stats ──────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
with col1:
    critical = len(df[df['competition_tier'] == 'Critical shortage'])
    st.metric("Fields with Critical Shortage", critical)
with col2:
    strong = len(df[df['competition_tier'] == 'Strong supply'])
    st.metric("Fields with Strong Supply", strong)
with col3:
    median_ratio = df['positions_per_100'].median()
    st.metric("Median Ratio", f"{median_ratio:.1f} per 100")

# ── Main Chart: Gap Ratio by Field ─────────────────────────────────────────
st.subheader(f"Funded Positions per 100 Qualified Applicants ({year})")

color_map = {
    'Strong supply': '#2ca02c',
    'Moderate': '#ff7f0e',
    'Competitive': '#d62728',
    'Critical shortage': '#7f0000',
}

fig = go.Figure()

for tier, color in color_map.items():
    tier_df = df[df['competition_tier'] == tier]
    if not tier_df.empty:
        fig.add_trace(go.Bar(
            x=tier_df['positions_per_100'],
            y=tier_df['broad_category'],
            orientation='h',
            name=tier,
            marker_color=color,
            customdata=tier_df[['funded_positions', 'qualified_applicants']],
            hovertemplate=(
                '<b>%{y}</b><br>'
                'Ratio: %{x:.1f} per 100 applicants<br>'
                'Funded positions: %{customdata[0]:,}<br>'
                'Qualified applicants: %{customdata[1]:,}<br>'
                '<extra></extra>'
            )
        ))

fig.update_layout(
    title=f'PhD Funding Competition Ratio by Field ({year})',
    xaxis_title='Funded Positions per 100 Qualified Applicants',
    yaxis_title='Field',
    barmode='stack',
    height=500,
    legend=dict(orientation='h', yanchor='bottom', y=1.02),
)

# Add reference lines
fig.add_vline(x=10, line_dash='dash', line_color='gray',
              annotation_text='Moderate threshold (10)')
fig.add_vline(x=5, line_dash='dash', line_color='red',
              annotation_text='Critical threshold (5)')

st.plotly_chart(fig, use_container_width=True)

# ── Trend Over Time ────────────────────────────────────────────────────────
st.subheader("Gap Trend Over Time")

@st.cache_data(ttl=3600)
def load_gap_trend() -> pd.DataFrame:
    try:
        conn = get_connection()
        return conn.execute("""
            SELECT
                broad_category,
                year,
                ROUND(
                    SUM(funded_positions)::DOUBLE / NULLIF(SUM(qualified_applicants), 0) * 100,
                    1
                ) AS positions_per_100
            FROM analytics_marts.mart_funding_gap
            WHERE year BETWEEN 2018 AND 2023
              AND qualified_applicants > 10
            GROUP BY 1, 2
            ORDER BY year, broad_category
        """).df()
    except Exception:
        return pd.DataFrame()

trend_df = load_gap_trend()
if not trend_df.empty:
    fig_trend = px.line(
        trend_df,
        x='year',
        y='positions_per_100',
        color='broad_category',
        title='Funding Gap Ratio Trend (2018–2023)',
        labels={'positions_per_100': 'Positions per 100 applicants'},
        markers=True,
    )
    st.plotly_chart(fig_trend, use_container_width=True)

# ── Data Table ──────────────────────────────────────────────────────────────
with st.expander("Full data table"):
    st.dataframe(
        df.sort_values('positions_per_100'),
        use_container_width=True,
        column_config={
            'positions_per_100': st.column_config.NumberColumn('Ratio', format='%.1f'),
            'funded_positions': st.column_config.NumberColumn('Funded Positions', format='%d'),
            'qualified_applicants': st.column_config.NumberColumn('Qualified Applicants', format='%d'),
        }
    )

# ── Methodology Note ───────────────────────────────────────────────────────
with st.expander("Methodology and limitations"):
    st.markdown("""
    **How funded positions are counted:**
    - NIH T32 training grants: estimated 1 position per $100K in award
    - NIH F31/F32 fellowships: 1 position per award (exact)
    - NSF grants >$300K: estimated 1 position per $100K
    - NSERC CGSD/CGSM: 1 position per award (exact)

    **How qualified applicants are counted:**
    - Source: IPEDS doctoral completions data (proxy for PhD pipeline)
    - Limitation: completions ≈ applicants, not enrollment
    - Better data: IPEDS Fall Enrollment by program (future improvement)

    **Known limitations:**
    - CAD → USD conversion uses fixed rate (0.74), not historical rates
    - NSF field mapping is keyword-based, not exact (≈85% accuracy)
    - T32 trainee count is estimated, not extracted from abstracts
    - Some grants fund both research and student training simultaneously

    **Confidence levels:**
    - HIGH: Direct 1:1 correspondence (fellowships, scholarships)
    - MEDIUM: Estimated from award amount
    """)
```

---

## Step 6.4 — Run All Gap Analysis Models

```bash
cd transform/scholarhub

# Run new models
dbt run --select int_funded_positions int_qualified_applicants mart_funding_gap

# Test
dbt test --select mart_funding_gap

# Verify
python -c "
import duckdb
conn = duckdb.connect('warehouse/scholarhub.duckdb', read_only=True)
result = conn.execute('''
    SELECT broad_category, country, year,
           funded_positions,
           qualified_applicants,
           positions_per_100_applicants,
           competition_tier
    FROM analytics_marts.mart_funding_gap
    WHERE year = 2022
    ORDER BY positions_per_100_applicants ASC
    LIMIT 10
''').df()
print(result.to_string())
"
```

---

## Phase 6 Checklist

- [ ] IPEDS data downloaded and loaded to `raw_ipeds_completions`
- [ ] `int_funded_positions` builds with rows from NSF + NIH + NSERC
- [ ] `int_qualified_applicants` builds with US doctoral completions
- [ ] `mart_funding_gap` has gap ratios by field and year
- [ ] Dashboard page 6 shows bar chart with color-coded competition tiers
- [ ] Methodology note is readable and honest about assumptions

---

## Complete Project Summary

You have now built:

```
✓ Phase 1: DuckDB warehouse + NSF extractor (raw zone, quality scoring)
✓ Phase 2: dbt pipeline (staging → intermediate → marts)
✓ Phase 3: Canada sources (NSERC + CIHR + NIH)
✓ Phase 4: Airflow orchestration (daily DAG, retry, health checks)
✓ Phase 5: Streamlit dashboard (5 pages, all business questions)
✓ Phase 6: Funding gap analysis (unique insight, 4-source join)
```

**Portfolio story in one paragraph:**

> "I built a data engineering pipeline that ingests research grant data from four federal APIs (NSF, NIH, NSERC, CIHR) and institutional enrollment data (IPEDS), normalizes it through a three-zone DuckDB warehouse managed by dbt, orchestrates daily runs with Airflow, and surfaces it through a Streamlit dashboard. The most technically interesting component is the funding gap analysis — deriving a competition ratio by field that requires joining four datasets with incompatible taxonomies (NSF program codes → CIP → IPEDS codes) and estimating 'funded positions' from grant amounts when the field doesn't exist directly in the data."

**What to show in interviews:**
1. The Airflow DAG UI (green tasks = proof of working pipeline)
2. The Pipeline Health page (proof of observability)
3. The dbt lineage graph (proof of dependency management)
4. The funding gap chart (proof of unique analytical thinking)
5. The dbt test output (proof of data quality mindset)
