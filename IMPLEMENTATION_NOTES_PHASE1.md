# Phase 1 Implementation Notes

**Date Completed:** March 24, 2026
**Duration:** ~2 hours
**Status:** ✅ Complete

---

## What We Built

### Directory Structure
```
scholarhub-de/
├── extractors/
│   ├── federal_apis/
│   │   └── nsf_extractor.py          ✅ Complete
│   ├── utils/
│   │   ├── logger.py                 ✅ Complete
│   │   ├── db_connection.py          ✅ Complete
│   │   └── rate_limiter.py           ✅ Complete
│   └── base.py                        ✅ Complete
├── warehouse/
│   └── init_warehouse.py             ✅ Complete
├── config/
│   └── settings.py                   ✅ Complete
├── tests/
│   ├── unit/test_nsf_extractor.py    ✅ Complete
│   └── fixtures/nsf_sample.json      ✅ Complete
├── data/raw/nsf/                      ✅ 500 awards extracted
├── pyproject.toml                     ✅ Complete
├── .env.example                       ✅ Complete
├── .gitignore                         ✅ Complete
└── venv/                              ✅ Python 3.13.5
```

### Final Metrics
- **Records Extracted:** 500 NSF awards (2020-present)
- **Quality Score:** 0.999 / 1.0
- **Extraction Time:** 69.12 seconds
- **Success Rate:** 100% (0 failures)
- **Test Coverage:** 5 unit tests created

---

## Issues Encountered & Solutions

### Issue 1: NSF API Returned 0 Records
**Problem:**
```bash
$ python -m extractors.federal_apis.nsf_extractor
# Result: records_found: 0, records_loaded: 0
```

**Root Cause:** NSF API requires at least one search parameter. Initial implementation had no search criteria, API rejected with error:
```json
{
  "notificationMessage": "At a minimum, one parametric key needs to be requested for the search results."
}
```

**Solution:**
Added search parameters to API request:
```python
params = {
    "printFields": ",".join(self.PRINT_FIELDS),
    "offset": offset,
    "dateStart": "01/01/2020",  # ← Added required search parameter
    "agency": "NSF"
}
```

**Learning:** Always test APIs with actual requests first. API documentation may not be clear about required vs optional parameters.

---

### Issue 2: NSF API Rejected `limit` Parameter
**Problem:**
```bash
$ curl "https://api.nsf.gov/services/v1/awards.json?limit=25&dateStart=01/01/2020"
# Error: "Invalid parameter(s) sent in the request. Invalid Parameter(s) {limit}"
```

**Root Cause:** NSF API doesn't accept a `limit` parameter. It returns a fixed 25 records per page by default.

**Solution:**
Removed `limit` parameter from API calls:
```python
# Before (failed):
params = {"offset": offset, "limit": 25, ...}

# After (worked):
params = {"offset": offset, ...}  # API returns 25 by default
```

**Learning:** API parameter names are not standardized. Test with curl/Postman before writing code.

---

### Issue 3: DuckDB Connection Singleton Not Closing
**Problem:**
Initial implementation kept connection open indefinitely, potentially causing lock issues when multiple extractors run.

**Solution:**
Implemented proper connection management:
```python
def close_connection():
    """Call this at the end of a process / Airflow task."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("duckdb_connection_closed")
```

**Learning:** DuckDB allows only ONE writer at a time. Always close connections or use context managers.

---

### Issue 4: Virtual Environment Required
**Problem:**
User attempted to install dependencies globally, which could pollute system Python.

**Solution:**
Created virtual environment first:
```bash
python3 -m venv venv
source venv/bin/activate
pip install duckdb requests python-dotenv pydantic-settings tenacity structlog tqdm pytest
```

**Learning:** Always use virtual environments for project isolation. Never install packages globally.

---

## Key Design Decisions

### 1. Why DuckDB over PostgreSQL?
- **Columnar storage** = 10-100x faster for analytics (GROUP BY, aggregations)
- **No server management** = Single `.duckdb` file
- **Perfect for local dev** before cloud migration

### 2. Why Structured Logging (structlog)?
```python
# Bad (unstructured):
print(f"Extracted {count} records")

# Good (structured):
logger.info("extraction_complete", records=count, duration=elapsed)
# Output: {"event": "extraction_complete", "records": 500, "duration": 69.12, "timestamp": "..."}
```
- Parseable by log aggregators (ELK, Splunk, CloudWatch)
- Easy to filter/search in production

### 3. Why Rate Limiting?
NSF API: 10 requests/min limit. Without rate limiting:
- API returns 429 Too Many Requests
- IP gets temporarily blocked

Token bucket implementation respects actual elapsed time vs naive `time.sleep()`.

### 4. Why Raw Zone Immutability?
```python
# Never:
UPDATE raw_nsf_awards SET ... WHERE id = ?

# Always:
INSERT INTO raw_nsf_awards (id, extracted_at, response_json, ...)
```
- Preserves full lineage
- Enables reprocessing if business logic changes
- Auditability for compliance

---

## Best Practices Demonstrated

✅ **Type hints everywhere** — `def extract(...) -> ExtractResult:`
✅ **Structured logging** — JSON output, not `print()`
✅ **Rate limiting** — Respect API limits
✅ **Retry logic** — Exponential backoff with tenacity
✅ **Quality scoring** — Data quality metrics (0.0-1.0)
✅ **Data archival** — JSON files + DuckDB
✅ **Testing** — Unit tests with mocked APIs
✅ **Configuration management** — Pydantic BaseSettings + .env

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `extractors/base.py` | 140 | Abstract base class for all extractors |
| `extractors/federal_apis/nsf_extractor.py` | 280 | NSF API extractor implementation |
| `extractors/utils/logger.py` | 40 | Structured logging setup |
| `extractors/utils/db_connection.py` | 50 | DuckDB connection singleton |
| `extractors/utils/rate_limiter.py` | 60 | Token bucket rate limiter |
| `warehouse/init_warehouse.py` | 180 | DuckDB schema initialization |
| `config/settings.py` | 30 | Pydantic settings |
| `tests/unit/test_nsf_extractor.py` | 200 | Unit tests |
| `tests/fixtures/nsf_sample.json` | 150 | Test fixture data |
| **Total** | **~1,130 lines** | |

---

## What Worked Well

✅ **Base extractor pattern** — Made testing easy with abstract class
✅ **Quality scoring** — Immediately identified data quality issues
✅ **Structured logging** — Easy to debug API issues
✅ **DuckDB** — Blazing fast, no setup required
✅ **Type hints** — Caught errors during development

---

## What Would We Do Differently?

### 1. API Testing Strategy
**Current:** Wrote code first, discovered API issues during execution
**Better:** Test API with curl first, then write code

### 2. Pagination Logic
**Current:** Hard-coded batch_size = 25
**Better:** Detect from first response or make configurable

### 3. Error Handling
**Current:** Logs errors but continues processing
**Consider:** Circuit breaker pattern to stop after N consecutive failures

---

## Time Breakdown

- **Environment Setup:** 15 min (venv, dependencies)
- **Warehouse Init:** 20 min (schema design, seed data)
- **Utility Modules:** 30 min (logger, db_connection, rate_limiter)
- **Base Extractor:** 25 min (abstract class, ExtractResult)
- **NSF Extractor:** 45 min (implementation + debugging API issues)
- **Testing:** 30 min (unit tests, fixtures)
- **First Extraction:** 5 min (run + verify)

**Total:** ~2 hours 50 minutes

---

## Portfolio Talking Points

When presenting this phase:

1. **"I implemented a production-grade data extractor with retry logic, rate limiting, and quality scoring"**
   - Shows understanding of real-world API constraints

2. **"I used structured logging for observability in distributed systems"**
   - Demonstrates knowledge beyond toy projects

3. **"The raw zone is immutable for full lineage tracking"**
   - Shows data engineering maturity (not just database CRUD)

4. **"I chose DuckDB over PostgreSQL for columnar analytics performance"**
   - Demonstrates architectural decision-making

---

## Next Steps → Phase 2

With Phase 1 complete, we have:
- ✅ Raw data in DuckDB (`raw_nsf_awards` table)
- ✅ 500 NSF awards with 0.999 quality
- ✅ Reusable extractor framework for future sources

Phase 2 will transform this raw JSON into analytics-ready tables using dbt.
