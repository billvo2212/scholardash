# Phase 3 Implementation Notes

**Date Completed:** March 24, 2026
**Duration:** ~1 hour
**Status:** ✅ Complete

---

## What We Built

### New Extractors
```
extractors/federal_apis/
├── nsf_extractor.py      ✅ Phase 1 (500 awards)
└── nih_extractor.py      ✅ Phase 3 (500 projects) ← NEW
```

### Updated dbt Models
```
transform/scholarhub/models/
├── staging/
│   ├── stg_nsf_awards.sql       ✅ Phase 2
│   └── stg_nih_projects.sql     ✅ Phase 3 ← NEW
├── intermediate/
│   └── int_all_awards.sql       ✅ Phase 3 ← NEW (unifies NSF + NIH)
└── marts/
    ├── mart_funding_by_institution.sql  ✅ Updated for multi-source
    ├── mart_funding_by_field.sql        ✅ Updated for multi-source
    └── mart_funding_by_year.sql         ✅ Updated for multi-source
```

### Final Metrics
- **Total Sources:** 2 (NSF + NIH)
- **Total Records:** 1,000 (500 NSF + 500 NIH)
- **Combined Quality Score:** 0.996
- **dbt Build Time:** 0.31 seconds (6 models)
- **NIH Extraction Speed:** 1.97s (incredibly fast!)

---

## Issues Encountered & Solutions

### Issue 1: NIH API Uses POST, Not GET
**Problem:**
NSF API uses GET requests with query parameters. NIH API documentation shows it uses POST with JSON body.

**Initial Attempt (failed):**
```python
# Tried to use same pattern as NSF:
response = self.session.get(url, params=search_criteria)
```

**Solution:**
Updated `base.py` to support both GET and POST:
```python
def fetch_with_retry(
    self,
    url: str,
    method: str = "GET",  # ← Added method parameter
    params: Optional[dict] = None,
    json_data: Optional[dict] = None,  # ← Added JSON body
    timeout: int = 30
) -> requests.Response:
    if method == "GET":
        response = self.session.get(url, params=params, timeout=timeout)
    elif method == "POST":
        response = self.session.post(url, json=json_data, params=params, timeout=timeout)
```

**NIH Extractor Usage:**
```python
search_criteria = {
    "criteria": {
        "fiscal_years": [fiscal_year],
    },
    "offset": offset,
    "limit": limit
}

response = self.fetch_with_retry(
    self.BASE_URL,
    method="POST",  # ← POST method
    json_data=search_criteria  # ← JSON body
)
```

**Learning:** RESTful doesn't always mean GET. Some APIs use POST for complex queries with many filters.

---

### Issue 2: NIH JSON Structure Different from NSF
**Problem:**
NIH organizes data in nested objects while NSF uses flat structure:

```json
// NSF (flat):
{
  "id": "2154321",
  "title": "...",
  "piFirstName": "Jane",
  "piLastName": "Smith"
}

// NIH (nested):
{
  "project_num": "R01CA123456",
  "project_title": "...",
  "contact_pi_name": "Jane Smith",
  "organization": {
    "org_name": "MIT",
    "city": "Cambridge",
    "state": "MA"
  }
}
```

**Solution:**
Parse nested JSON in staging model:
```sql
-- Nested object parsing:
TRY_CAST(json_extract_string(response_json, '$.organization.org_name') AS VARCHAR) AS organization,
TRY_CAST(json_extract_string(response_json, '$.organization.city') AS VARCHAR) AS city,
```

**Learning:** Expect schema heterogeneity across sources. Design staging layer to normalize to common schema.

---

### Issue 3: dbt Backup Table Error
**Problem:**
First `dbt run` after adding NIH staging failed:
```
Runtime Error: Table with name mart_funding_by_institution__dbt_backup does not exist!
```

**Root Cause:**
dbt was in middle of updating materialized table when we changed dependencies. Backup table reference got orphaned.

**Solution:**
```bash
# Full refresh rebuilds all tables from scratch:
dbt run --full-refresh
# ✅ Success: All 6 models built
```

**Learning:** When changing model dependencies, use `--full-refresh` to avoid backup table issues.

---

### Issue 4: Column Name Standardization
**Problem:**
NSF uses `institution`, NIH uses `organization`. How to unify?

**Solution:**
Create intermediate layer (`int_all_awards.sql`) with common schema:
```sql
-- NSF:
institution AS institution,

-- NIH:
organization AS institution,  -- ← Renamed to match NSF

-- Both now have consistent column name
```

**Learning:** Intermediate layer is WHERE you solve schema differences. Don't push heterogeneity to marts.

---

## Key Design Decisions

### 1. Intermediate Layer for Multi-Source Unification
```
Staging (source-specific)      Intermediate (unified)       Marts (analytics)
  stg_nsf_awards ────┐
                     ├──→ int_all_awards ──→ mart_funding_by_*
  stg_nih_projects ───┘
```

**Why?**
- Marts don't need to know about source differences
- Single place to handle schema mapping
- Easy to add 3rd, 4th source later

### 2. Source Breakdown in Marts
Added NSF/NIH split to all mart models:
```sql
COUNT(CASE WHEN source = 'NSF' THEN 1 END) AS nsf_awards,
COUNT(CASE WHEN source = 'NIH' THEN 1 END) AS nih_awards,
SUM(CASE WHEN source = 'NSF' THEN funding_amount ELSE 0 END) AS nsf_funding,
SUM(CASE WHEN source = 'NIH' THEN funding_amount ELSE 0 END) AS nih_funding,
```

**Why?**
- Enables cross-source comparisons
- Answers: "Which institutions get both NSF and NIH funding?"
- Shows data source diversity

### 3. Quality Scoring Per Source
NSF and NIH have different schemas, so quality scoring logic differs:

**NSF Quality (emphasizes PI name):**
```python
if award.get("piFirstName") and award.get("piLastName"):
    score += 0.3
```

**NIH Quality (PI name combined):**
```python
contact_pi = project.get("contact_pi_name")
if contact_pi:
    score += 0.3
```

**Result:**
- NSF: 0.999 avg quality
- NIH: 0.993 avg quality
- Combined: 0.996 avg quality

---

## Architecture Evolution

### Before Phase 3 (Single Source)
```
NSF API → raw_nsf_awards → stg_nsf_awards → mart_*
```

### After Phase 3 (Multi-Source)
```
NSF API ─→ raw_nsf_awards ──→ stg_nsf_awards ──┐
                                                ├──→ int_all_awards ──→ mart_*
NIH API ─→ raw_nih_projects ─→ stg_nih_projects┘
```

**Benefits:**
- Marts query single table (`int_all_awards`)
- Easy to add NSERC, CIHR, etc. later
- Cross-source comparisons without complex JOINs

---

## SQL Patterns Worth Noting

### Pattern 1: CASE-Based Source Aggregation
```sql
-- Count by source:
COUNT(CASE WHEN source = 'NSF' THEN 1 END) AS nsf_awards

-- Sum by source:
SUM(CASE WHEN source = 'NIH' THEN funding_amount ELSE 0 END) AS nih_funding
```

Better than:
```sql
-- Slower (two subqueries):
(SELECT COUNT(*) FROM awards WHERE source = 'NSF') AS nsf_awards
```

### Pattern 2: UNION ALL for Multi-Source
```sql
WITH nsf_awards AS (...),
     nih_projects AS (...)

SELECT * FROM nsf_awards
UNION ALL  -- ← Not UNION (no dedup needed, faster)
SELECT * FROM nih_projects
```

### Pattern 3: COALESCE for Null Handling
```sql
-- Handle inconsistent field names:
COALESCE(org_city, city) AS institution_city
```

---

## Files Created/Modified

| File | Lines | Status |
|------|-------|--------|
| `extractors/federal_apis/nih_extractor.py` | 290 | ✅ Created |
| `models/staging/stg_nih_projects.sql` | 110 | ✅ Created |
| `models/intermediate/int_all_awards.sql` | 80 | ✅ Created |
| `models/marts/mart_funding_by_institution.sql` | +10 | ✅ Modified |
| `models/marts/mart_funding_by_field.sql` | +5 | ✅ Modified |
| `models/marts/mart_funding_by_year.sql` | +10 | ✅ Modified |
| `extractors/base.py` | +5 | ✅ Modified (added POST support) |
| **Total** | **~510 lines** | |

---

## What Worked Well

✅ **Base extractor reusability** — NIH extractor inherited 80% from base class
✅ **dbt dependency graph** — Automatically rebuilt downstream marts
✅ **Intermediate layer pattern** — Clean separation of concerns
✅ **Fast iteration** — NIH extractor working in <30 min

---

## What Would We Do Differently?

### 1. Schema Registry
**Current:** Implicit schema mapping in SQL
**Better:** Explicit schema registry:
```yaml
# schemas/unified_award.yml
columns:
  - name: institution
    nsf_source: institution
    nih_source: organization.org_name
    nserc_source: institution_name
```

### 2. Data Quality Dashboard
**Current:** Quality scores logged, not visualized
**Better:** Track quality trends over time:
```sql
CREATE VIEW quality_trends AS
SELECT
    DATE_TRUNC('day', extracted_at) AS date,
    source,
    AVG(quality_score) AS avg_quality
FROM int_all_awards
GROUP BY 1, 2
ORDER BY 1 DESC
```

### 3. Source-Specific Validators
**Current:** Generic quality scoring
**Better:** Source-specific validation rules:
```python
class NSFValidator:
    def validate(self, award):
        assert award.get('id').startswith('2')  # NSF IDs start with 2

class NIHValidator:
    def validate(self, project):
        assert project.get('project_num').startswith('R')  # R01, R21, etc.
```

---

## Cross-Source Analytical Capabilities Unlocked

### 1. Institution Diversification
```sql
SELECT
    institution,
    nsf_awards,
    nih_awards,
    CASE
        WHEN nsf_awards > 0 AND nih_awards > 0 THEN 'Diversified'
        WHEN nsf_awards > 0 THEN 'NSF Only'
        WHEN nih_awards > 0 THEN 'NIH Only'
    END AS funding_profile
FROM mart_funding_by_institution
WHERE total_awards > 5
ORDER BY (nsf_funding + nih_funding) DESC
```

### 2. Funding Mix Trends
```sql
SELECT
    award_year,
    nsf_funding,
    nih_funding,
    ROUND(100.0 * nsf_funding / (nsf_funding + nih_funding), 1) AS nsf_pct,
    ROUND(100.0 * nih_funding / (nsf_funding + nih_funding), 1) AS nih_pct
FROM mart_funding_by_year
ORDER BY award_year
```

### 3. Biomedical vs General Science Split
```sql
-- NIH ≈ biomedical, NSF ≈ general science
SELECT
    source,
    COUNT(*) AS awards,
    SUM(funding_amount) AS total_funding,
    AVG(funding_amount) AS avg_award
FROM int_all_awards
GROUP BY source
```

---

## Time Breakdown

- **NIH Extractor:** 30 min
- **NIH Extraction Run:** 2 min (1.97s actual)
- **stg_nih_projects.sql:** 20 min
- **int_all_awards.sql:** 15 min
- **Mart Updates:** 20 min
- **dbt Rebuild & Testing:** 10 min

**Total:** ~1 hour 37 minutes

---

## Portfolio Talking Points

When presenting this phase:

1. **"I integrated heterogeneous data sources (GET and POST APIs) into a unified schema"**
   - Shows ability to handle real-world complexity

2. **"The intermediate layer normalizes schema differences so marts don't need source-specific logic"**
   - Demonstrates layered architecture understanding

3. **"I added source breakdowns to enable cross-source analysis without complex JOINs"**
   - Shows analytical thinking

4. **"The pipeline now supports 2 sources with 1,000 records and 0.996 combined quality"**
   - Quantifiable results

---

## Common Multi-Source Interview Questions - Our Answers

**Q: How do you handle schema evolution when a source changes?**
A: Isolation via staging layer. NSF schema changes only affect `stg_nsf_awards`. Intermediate layer absorbs the change.

**Q: How do you deduplicate across sources?**
A: Intermediate layer uses `UNION ALL` (no auto-dedup). If same entity appears in both sources, we'd add deduplication logic based on business rules (e.g., PI name + institution + year).

**Q: What if sources have overlapping data?**
A: Currently we `UNION ALL` (keep both). For actual deduplication, we'd use:
```sql
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY pi_name, institution, award_year
    ORDER BY quality_score DESC
) = 1
```

**Q: How do you handle late-arriving data from different sources?**
A: Each source tracks `extracted_at`. Incremental models with `max(extracted_at)` per source ensure we only process new data.

---

## Next Steps → Phase 4

With Phase 3 complete, we have:
- ✅ 2 data sources (NSF, NIH)
- ✅ 1,000 unified awards
- ✅ Cross-source analytics enabled
- ✅ 0.996 combined quality

Phase 4 will automate the pipeline with **Apache Airflow** to:
- Schedule daily extractions
- Orchestrate dbt runs
- Monitor data quality
- Alert on failures
