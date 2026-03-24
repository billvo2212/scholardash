# CLAUDE.md — ScholarHub DE Project Context

> This file is the single source of truth for any AI assistant (Claude or otherwise)
> working on this project. Read it fully before writing any code.

---

## What This Project Is

**ScholarHub** is a **data engineering portfolio project** with a useful product as output.

- **Primary goal:** Demonstrate production-grade DE skills — heterogeneous ingestion, Kimball modeling, dbt transforms, Airflow orchestration, analytical dashboards
- **Secondary goal:** A real platform helping graduate students find funded research positions in North America by combining federal grant data with university funding information

It is **not** a startup. It is **not** trying to beat Fastweb or ProFellow on UI. It wins on **data depth** no competitor has: federal grant intelligence joined with graduate position data.

---

## The Core Differentiator

No existing platform joins these three data layers:

```
Layer 1: Federal grant databases (NSF, NIH, NSERC, CIHR, SSHRC)
         → Who has money to fund students RIGHT NOW

Layer 2: University graduate funding pages (scraped)
         → What positions are publicly posted

Layer 3: Institutional enrollment data (IPEDS, StatCan)
         → How many applicants are competing for those positions
```

The intersection of these three layers enables **Funding Gap Analysis** — the ratio of funded positions to qualified applicants by field. Nobody is computing this today because it requires a real DE pipeline, not just a scraper.

---

## Business Questions This Pipeline Answers

### Unique (only we can answer — highest priority)
| ID | Question | Data Required | Uniqueness |
|----|----------|--------------|------------|
| BQ-1 | Which professors are actively hiring PhD students RIGHT NOW (before posting a public ad)? | NSF T32 + NIH R01 + NSERC CREATE grants | Nobody joins federal grant data with professor profiles |
| BQ-2 | Which fields are growing or shrinking in funding over time? | NSF 1989–present, NIH 2000–present, NSERC 1991–present | Historical depth + multi-source join |
| BQ-3 | Where are the funding gaps — funded positions per qualified applicant, by field? | Grant data + IPEDS/StatCan enrollment | Requires 4-dataset join with taxonomy resolution |

### Better (we answer better than competitors)
| ID | Question | Why We're Better |
|----|----------|-----------------|
| BQ-4 | What month is best to apply by field? | 35 years of grant cycle data, not opinion |
| BQ-5 | Which institutions have the most active funded capacity? | Active grants, not just posted listings |
| BQ-6 | How does funding vary by province/state? | Geographic join with federal grant geo data |

### Pipeline Health (internal, critical for portfolio)
| ID | Question | Purpose |
|----|----------|---------|
| BQ-7 | Is our pipeline healthy? | Proves observable, production-grade system |
| BQ-8 | What is data quality per source? | Shows DE maturity beyond "it runs" |

---

## Tech Stack — Never Change Without Good Reason

```
Language:        Python 3.11+
Data warehouse:  DuckDB (local file-based columnar DB)
Transforms:      dbt-duckdb (SQL managed as code)
Orchestration:   Apache Airflow 2.8+ (Docker)
Dashboard:       Streamlit + Plotly
Testing:         pytest + dbt tests
Package mgmt:    uv (fast) or poetry
```

**Why DuckDB over PostgreSQL for analytics:**
- Columnar storage = 10-100x faster for GROUP BY / aggregation queries
- No server to manage — single `.duckdb` file
- Native Parquet read/write
- dbt-duckdb adapter is mature and well-maintained
- Perfect for local development before cloud migration

**Why Airflow over simpler schedulers (cron, Prefect):**
- Industry standard — shows up in every DE job description
- DAG visualization is portfolio-presentable
- Retry/backfill/monitoring built-in
- Docker deployment teaches real orchestration patterns

---

## Data Sources by Priority

### Tier 1 — Start Here (Structured APIs, No Auth)
| Source | URL | Format | Key Fields |
|--------|-----|--------|-----------|
| NSF Award Search | `api.nsf.gov/services/v1/awards.json` | JSON REST | award_id, PI, institution, amount, dates, abstract, program |
| NIH RePORTER v2 | `api.reporter.nih.gov/v2/projects/search` | JSON POST | project_num, PI, org, total_cost, activity_code, abstract |
| USASpending.gov | `api.usaspending.gov/api/v2/` | JSON REST | award_amount, recipient, agency, geography |

### Tier 2 — Canada (Bulk Downloads)
| Source | URL | Format | Notes |
|--------|-----|--------|-------|
| NSERC Awards DB | `nserc-crsng.gc.ca/ase-oro/` | CSV bulk | All NSERC grants since 1991 |
| CIHR Open Data | `open.canada.ca` dataset `49edb1d7` | CSV annual | Biomedical research funding |
| Canada Open Gov | `open.canada.ca` dataset `432527ab` | CSV + CKAN API | All federal grants consolidated |

### Tier 3 — Enrichment (Add in Phase 5+)
| Source | URL | Notes |
|--------|-----|-------|
| IPEDS (US) | `nces.ed.gov/ipeds/` | Enrollment data for gap analysis |
| Statistics Canada | `www150.statcan.gc.ca` Table 37-10-0011 | Canadian enrollment |
| Semantic Scholar | `api.semanticscholar.org/graph/v1/` | Professor h-index, publications |

---

## Data Architecture — Storage Zones

```
RAW ZONE         → Never mutated. JSON/CSV exactly as received from source.
                   Table prefix: raw_*
                   Also archived to: data/raw/{source}/{date}/

STAGING ZONE     → Parsed, typed, quality-scored. Not deduplicated.
                   Table prefix: stg_*
                   Managed by: dbt (models/staging/)

INTERMEDIATE     → Business logic, cross-source joins, entity resolution.
                   Table prefix: int_*
                   Managed by: dbt (models/intermediate/)

MART ZONE        → Pre-aggregated, optimized for dashboard queries.
                   Table prefix: mart_*
                   Managed by: dbt (models/marts/)
```

**Immutability rule:** Raw zone tables are append-only. Never UPDATE or DELETE a raw record. If source data changes, that is a new raw record. This preserves full lineage and enables reprocessing.

---

## Data Model — Kimball Fact/Dimension Schema

### Why Fact/Dim (not just normalized tables)?

Normalized (3NF) tables are optimized for write performance and storage. Kimball star schema is optimized for **read/analytical performance**. When a dashboard asks "total funding by field by year," a star schema answers it with one JOIN. Normalized tables require 5+ JOINs.

Rule: **Fact tables contain measures (numbers you sum/count/avg). Dimension tables contain context (who, what, where, when).**

### Fact Tables
```sql
fact_funding_opportunity   -- A funding position exists and was discovered
fact_professor_grant       -- A professor received a grant (NSF/NIH/NSERC)
fact_crawl_event           -- Pipeline crawled a source (observability)
```

### Dimension Tables
```sql
dim_date                   -- Date spine: 2015-01-01 to 2030-12-31
dim_institution            -- Universities and funding agencies
dim_academic_field         -- CIP code taxonomy + broad categories
dim_funding_agency         -- NSF, NIH, NSERC, CIHR, SSHRC metadata
dim_professor              -- Professor profiles
dim_source                 -- Data source registry (tier, type, health)
```

---

## Project Phases Overview

| Phase | Focus | Duration | Output |
|-------|-------|----------|--------|
| 1 | Foundations: DuckDB + NSF extractor | Week 1–2 | Raw pipeline running, first data in DuckDB |
| 2 | dbt transforms: staging → marts | Week 2–3 | mart_funding_by_field queryable |
| 3 | Canada sources: NSERC + CIHR | Week 3–4 | North America coverage complete |
| 4 | Airflow orchestration | Week 4–5 | Fully automated daily pipeline |
| 5 | Streamlit dashboard (5 pages) | Week 5–6 | Visual answers to all business questions |
| 6 | Funding gap analysis | Week 6–7 | Unique BQ-3: gap ratio by field |

Each phase has its own detailed markdown file in `docs/`.

---

## Code Conventions

### Python
```python
# All extractors inherit BaseExtractor
# All extractors write to raw_* tables first, never directly to staging
# Use type hints everywhere
# Structured logging (JSON), never print()
# Rate limiting on all external API calls
```

### SQL / dbt
```sql
-- Staging models: one model per source, inputs are raw_* tables only
-- Intermediate models: cross-source joins, entity resolution
-- Mart models: GROUP BY aggregations only, no business logic
-- All models include a data_quality_score or row_count assertion
```

### dbt naming
```
stg_{source}_{entity}.sql     e.g. stg_nsf_awards.sql
int_{entity}_{transformation}.sql   e.g. int_professors_unified.sql
mart_{business_question}.sql  e.g. mart_funding_by_field.sql
dim_{entity}.sql              e.g. dim_date.sql
fact_{event}.sql              e.g. fact_professor_grant.sql
```

---

## Portfolio Narrative

When presenting this project, the story is:

> "I built a data pipeline that ingests federal research grant data from NSF, NIH, and NSERC APIs, normalizes it through a staged DuckDB warehouse managed by dbt, orchestrates it with Airflow, and surfaces it through a Streamlit dashboard answering business questions no competitor answers — including a funding gap analysis that requires joining four heterogeneous datasets with incompatible taxonomies. The technical challenge was not the ingestion; it was the entity resolution, taxonomy mapping, and making the pipeline observable and trustworthy."

**What interviewers will probe:**
- "How do you handle source schema changes?" → Raw zone immutability + staging layer isolation
- "How do you know the data is correct?" → dbt tests, data quality scores, pipeline health dashboard
- "What would you do differently at 10x scale?" → Move DuckDB → Snowflake/BigQuery, Airflow → managed service
- "Why DuckDB over PostgreSQL?" → Columnar analytics, no server management, portability

---

## Running the Project

```bash
# 1. Setup
git clone <repo>
cd scholarhub-de
cp .env.example .env
pip install uv && uv sync   # or: pip install -r requirements.txt

# 2. Initialize warehouse
python warehouse/init_warehouse.py

# 3. Run first extraction
python -m extractors.federal_apis.nsf_extractor

# 4. Run dbt transforms
cd transform && dbt run && dbt test

# 5. Start dashboard
streamlit run dashboard/app.py

# 6. Start Airflow (requires Docker)
docker-compose up -d
# Visit: http://localhost:8080 (admin/admin)
```

---

## Key Design Decisions & Why

| Decision | Alternative Considered | Reason Chosen |
|----------|----------------------|---------------|
| DuckDB as warehouse | PostgreSQL, SQLite | Columnar analytics, portability, dbt-duckdb maturity |
| dbt for SQL management | Raw Python scripts | Dependency graph, testing, documentation generation |
| Airflow for orchestration | Cron + shell scripts | Industry standard, DAG visualization, retry logic |
| Streamlit for dashboard | Plotly Dash, Metabase | Python-native, fast iteration, free hosting |
| Star schema (Kimball) | Wide flat tables | Query performance, cross-team clarity, textbook correctness |
| Raw zone immutability | Upsert-in-place | Full lineage, reprocessability, auditability |

---

## Files Reference

```
scholarhub-de/
├── CLAUDE.md                    ← This file
├── docs/
│   ├── PHASE_1_foundations.md   ← Setup + NSF extractor
│   ├── PHASE_2_dbt_transforms.md ← dbt staging/marts
│   ├── PHASE_3_canada_sources.md ← NSERC + CIHR
│   ├── PHASE_4_airflow.md       ← Orchestration
│   ├── PHASE_5_dashboard.md     ← Streamlit 5 pages
│   └── PHASE_6_gap_analysis.md  ← Funding gap BQ-3
├── extractors/
├── transform/                   ← dbt project
├── warehouse/
├── dags/
├── dashboard/
├── data/
│   ├── raw/
│   └── exports/
├── tests/
├── config/
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```
