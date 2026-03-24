# Future Enhancements

This document outlines planned features and improvements for the ScholarHub data pipeline.

---

## 1. Canadian Funding Sources (High Priority)

**Objective:** Expand from US-only to full North American coverage.

### NSERC Integration
**Source:** Natural Sciences and Engineering Research Council of Canada
**URL:** https://www.nserc-crsng.gc.ca/ase-oro/index_eng.asp
**Format:** CSV bulk download (annual)
**Data Coverage:** 1991-present (~150,000+ awards)

**Implementation Plan:**
```python
# New extractor
extractors/federal_apis/nserc_extractor.py
  - Download CSV from NSERC portal
  - Parse CSV → raw_nserc_awards table
  - Handle bilingual institution names (EN/FR)
  - Currency conversion: CAD → USD

# New dbt models
models/staging/stg_nserc_awards.sql
  - Parse NSERC-specific schema
  - Map research areas to program_name
  - Standardize institution names

# Update int_all_awards
UNION ALL stg_nserc_awards (3rd source)
```

**Effort Estimate:** 2-3 hours
**Data Volume:** ~5,000-10,000 awards (2020-2024)

---

### CIHR Integration
**Source:** Canadian Institutes of Health Research
**URL:** https://open.canada.ca/data/en/dataset/49edb1d7
**Format:** CSV via Open Canada portal
**Data Coverage:** Annual releases, biomedical research

**Implementation Plan:**
```python
# New extractor
extractors/federal_apis/cihr_extractor.py
  - Download CSV from Open Canada
  - Parse → raw_cihr_projects table
  - Map to NIH-equivalent structure

# New dbt models
models/staging/stg_cihr_projects.sql
  - Similar to stg_nih_projects
  - Biomedical focus

# Update int_all_awards
UNION ALL stg_cihr_projects (4th source)
```

**Effort Estimate:** 2-3 hours
**Data Volume:** ~3,000-5,000 projects (annual)

---

### Airflow DAG Updates
```python
# dags/scholarhub_pipeline.py
extract_nsf >> extract_nih >> extract_nserc >> extract_cihr >> dbt_run
```

**Total Implementation:** 4-6 hours for both sources
**Result:** True North American coverage (US + Canada)

---

## 2. Funding Gap Analysis (Phase 6 - Optional)

**Objective:** Calculate funded positions per qualified applicant by field.

**BQ-3:** Where are the funding gaps — positions per applicant?

**Required Data Sources:**
- IPEDS (US graduate enrollment by institution × field)
- Statistics Canada (Canadian enrollment data)
- CIP code taxonomy mapping

**Key Challenge:** Mapping NSF/NIH/NSERC program names → CIP codes (200+ mappings)

**Effort Estimate:** 8-12 hours
**Value:** Unique competitive differentiator (no other platform has this)

**Status:** Designed but not prioritized for initial release

---

## 3. Incremental dbt Models

**Current:** Full refresh on every `dbt run`
**Better:** Incremental materialization for large tables

```sql
-- models/intermediate/int_all_awards.sql
{{ config(
    materialized='incremental',
    unique_key='unified_award_id'
) }}

SELECT * FROM source
{% if is_incremental() %}
    WHERE extracted_at > (SELECT MAX(extracted_at) FROM {{ this }})
{% endif %}
```

**Benefit:** Faster builds when extracting millions of records
**Effort:** 1-2 hours

---

## 4. Professor-Level Entity Resolution

**Current:** PI names stored as text (duplicates if spelled differently)
**Better:** Deduplicate and enrich professor entities

**Implementation:**
```sql
-- New dimension table
dim_professor (
    professor_id,          -- Generated unique ID
    canonical_name,        -- Standardized name
    institutions,          -- Array of affiliations
    total_awards,          -- Lifetime award count
    total_funding,         -- Lifetime funding amount
    h_index,              -- From Semantic Scholar API
    research_areas         -- Aggregated from grants
)
```

**Data Sources:**
- Existing grant data (PI names)
- Semantic Scholar API (publications, h-index)
- ORCID (unique researcher IDs)

**Benefit:** "Which professors are most successful?" analytics
**Effort:** 4-6 hours

---

## 5. Semantic Search on Abstracts

**Current:** Keyword search only
**Better:** Semantic search using embeddings

**Implementation:**
- Generate embeddings for all grant abstracts (OpenAI/Cohere API)
- Store embeddings in DuckDB vector column
- Enable "find grants similar to my research" queries

**Example Query:**
```python
"Find grants about using deep learning for drug discovery"
# Returns semantically similar grants, not just keyword matches
```

**Effort:** 3-4 hours
**Cost:** ~$5-10 for embedding generation (one-time)

---

## 6. Alerting & Notifications

**Use Case:** "Notify me when a professor in my field gets a new grant"

**Implementation:**
- User profiles with research interests
- Daily cron job comparing new awards to user interests
- Email/Slack notifications for matches

**Tech Stack:**
- Airflow sensor for new data
- Simple email service (SendGrid/Mailgun)
- User preference storage (SQLite or DuckDB)

**Effort:** 4-5 hours

---

## 7. Historical Trend Analysis

**Current:** Limited to available data (2020-2024)
**Better:** Historical depth (NSF: 1989+, NIH: 2000+)

**Challenge:** Older data requires different APIs/formats
**Benefit:** 35+ years of funding trends
**Data Volume:** ~5M+ awards
**Storage:** 50-100 GB (would require cloud database)

**Migration Path:**
- DuckDB → Snowflake/BigQuery for scale
- Incremental extraction to avoid re-downloading old data

---

## 8. Dashboard Enhancements

### Planned Features:
- **Search autocomplete** for institutions/professors
- **Bookmarking** favorite grants/professors
- **Export to Excel** with formatting
- **Mobile-responsive** layout improvements
- **Dark mode** toggle
- **Comparison mode** (compare 2-3 institutions side-by-side)

**Effort:** 2-3 hours total

---

## 9. Data Quality Monitoring Dashboard

**Current:** Pipeline health page shows basic metrics
**Better:** Dedicated data quality dashboard with:
- Anomaly detection (sudden drop in award counts?)
- Data freshness SLAs (alert if >48 hours stale)
- Quality score trends over time
- Source reliability metrics

**Tools:** Great Expectations or custom dbt tests
**Effort:** 3-4 hours

---

## 10. API Layer

**Use Case:** Allow external applications to query ScholarHub data

**Implementation:**
```python
# FastAPI or Flask REST API
GET /api/v1/awards?institution=MIT&year=2024
GET /api/v1/professors?field=machine-learning
GET /api/v1/trends/funding-by-field
```

**Benefits:**
- Portfolio demonstrates API design skills
- Enables third-party integrations
- Could monetize access

**Effort:** 4-6 hours

---

## Priority Ranking

| Enhancement | Impact | Effort | Priority |
|-------------|--------|--------|----------|
| 1. Canadian sources (NSERC/CIHR) | High | 4-6h | **🔴 High** |
| 2. Incremental dbt models | Medium | 1-2h | Medium |
| 3. Professor entity resolution | High | 4-6h | Medium |
| 4. Dashboard improvements | Medium | 2-3h | Medium |
| 5. Semantic search | High | 3-4h | Low |
| 6. Funding gap analysis (Phase 6) | Very High | 8-12h | Low |
| 7. Historical data (1989+) | Medium | 8-10h | Low |
| 8. Alerting/notifications | Medium | 4-5h | Low |
| 9. Data quality monitoring | Low | 3-4h | Low |
| 10. API layer | Medium | 4-6h | Low |

**Recommended Next Steps:**
1. Add NSERC for Canadian coverage (2-3 hours)
2. Improve dashboard UX (2-3 hours)
3. Deploy to production (see deployment options)

---

## Notes

- All estimates assume familiarity with the existing codebase
- Some features (e.g., historical data) require infrastructure changes
- Phase 6 (funding gap) remains the biggest differentiator but highest effort
- Current project is **production-ready** without these enhancements
