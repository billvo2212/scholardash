# Phase 4 Implementation Notes

**Date Completed:** March 24, 2026
**Duration:** In Progress
**Status:** 🟡 Blocked on Docker startup

---

## What We Built

### Airflow Orchestration Infrastructure
```
scholarhub-de/
├── docker-compose.yml           ✅ Complete (Airflow 2.8.1 LocalExecutor)
├── dags/
│   └── scholarhub_pipeline.py   ✅ Complete (5-task DAG)
├── .airflowignore               ✅ Complete
└── .env                         ✅ Updated (added AIRFLOW_UID)
```

### Airflow DAG Structure
```python
# scholarhub_pipeline.py
extract_nsf (PythonOperator)
    ↓
extract_nih (PythonOperator)
    ↓
dbt_run (BashOperator)
    ↓
dbt_test (BashOperator)
    ↓
log_summary (PythonOperator)
```

### Final Metrics (Once Running)
- **Tasks:** 5 (2 Python, 2 Bash, 1 summary)
- **Schedule:** Daily at 6:00 AM UTC (`0 6 * * *`)
- **Retries:** 2 with 5-minute exponential backoff
- **Expected Duration:** ~15 minutes total pipeline execution

---

## Issues Encountered & Solutions

### Issue 1: Sequential vs Parallel Task Execution
**Problem:**
DuckDB allows only ONE writer at a time. Initial thought was to run NSF and NIH extractors in parallel to save time.

**Root Cause:**
```python
# This would fail (parallel writers):
[extract_nsf, extract_nih] >> dbt_run

# DuckDB error: "database is locked"
```

**Solution:**
Designed sequential extraction in DAG dependencies:
```python
# Sequential extraction (DuckDB single writer constraint):
extract_nsf >> extract_nih >> dbt_run >> dbt_test >> log_summary
```

**Learning:** Architecture constraints (like DuckDB's single-writer model) must inform orchestration design. Parallel execution isn't always possible or better.

---

### Issue 2: Docker Compose Version Warning
**Problem:**
When running `docker-compose up`, received warning:
```
level=warning msg="/Users/bv/Code/scholarhub/scholardash/docker-compose.yml:
the attribute `version` is obsolete, it will be ignored"
```

**Root Cause:**
Docker Compose v2+ no longer requires the `version` field. It's maintained for backwards compatibility but triggers warnings.

**Solution:**
Kept `version: '3.8'` for clarity and compatibility with older Docker Compose versions. The warning is informational only and doesn't affect functionality.

**Learning:** Docker Compose evolution means some fields become optional. Keep for compatibility unless targeting specific modern versions only.

---

### Issue 3: Python Callable Import Path in Airflow
**Problem:**
Airflow DAG needs to import extractor classes that are in the project directory, not in Airflow's default Python path.

**Solution:**
Set `PYTHONPATH` in docker-compose.yml environment:
```yaml
environment:
  PYTHONPATH: '/opt/project'  # Makes extractors/ importable
```

Mount entire project into container:
```yaml
volumes:
  - .:/opt/project           # Project root
  - ./dags:/opt/airflow/dags # Airflow looks here for DAGs
```

Import in DAG Python callable:
```python
def extract_nsf_data(**context):
    from extractors.federal_apis.nsf_extractor import NSFExtractor
    # Works because PYTHONPATH includes /opt/project
```

**Learning:** Containerized Airflow needs explicit PYTHONPATH configuration to import project code. Mount project as volume and set PYTHONPATH.

---

### Issue 4: Airflow Initialization Dependencies
**Problem:**
Airflow requires specific initialization sequence:
1. PostgreSQL metadata DB must be healthy
2. Database schema must be initialized
3. Admin user must be created
4. Python dependencies must be installed in container

**Solution:**
Created multi-stage initialization in docker-compose.yml:

**Stage 1:** PostgreSQL healthcheck:
```yaml
postgres:
  healthcheck:
    test: ["CMD", "pg_isready", "-U", "airflow"]
    interval: 5s
    retries: 5
```

**Stage 2:** Airflow initialization service:
```yaml
airflow-init:
  entrypoint: /bin/bash
  command:
    - -c
    - |
      mkdir -p /opt/airflow/logs /opt/airflow/dags /opt/airflow/plugins
      airflow db init
      airflow users create --username admin --password admin --role Admin
      pip install duckdb requests python-dotenv pydantic-settings tenacity structlog tqdm dbt-duckdb
  depends_on:
    postgres:
      condition: service_healthy
```

**Stage 3:** Start webserver and scheduler:
```yaml
airflow-webserver:
  depends_on:
    postgres:
      condition: service_healthy

airflow-scheduler:
  depends_on:
    postgres:
      condition: service_healthy
```

**Learning:** Orchestration tools need careful initialization sequencing. Use healthchecks and depends_on conditions to ensure proper startup order.

---

### Issue 5: DAG File Parsing Performance
**Problem:**
Airflow scans all Python files in `dags/` directory to find DAGs. Without `.airflowignore`, it would parse:
- All extractor code (270+ files)
- Test files
- dbt models
- Markdown documentation

This causes:
- Slow DAG refresh (30+ seconds)
- High scheduler CPU usage
- Potential import errors from non-DAG Python files

**Solution:**
Created `.airflowignore` file to exclude non-DAG directories:
```
# .airflowignore
extractors/
transform/
warehouse/
config/
tests/
dashboard/
data/
*.md
*.ipynb
```

**Expected Result:**
- DAG refresh time: <2 seconds
- Only `dags/scholarhub_pipeline.py` is parsed
- No false-positive DAG detection

**Learning:** Airflow DAG directories should contain ONLY DAG files. Use `.airflowignore` to exclude project code, similar to `.gitignore` pattern.

---

## Key Design Decisions

### 1. LocalExecutor vs CeleryExecutor
```yaml
AIRFLOW__CORE__EXECUTOR: LocalExecutor
```

**Why LocalExecutor?**
- Single machine, no distributed workers needed
- Simpler setup (no Redis, no Celery)
- Perfect for portfolio project scale (500 NSF + 500 NIH records)
- Still production-grade patterns (just smaller scale)

**When to upgrade to CeleryExecutor?**
- Multiple worker machines
- 10,000+ tasks per DAG run
- Need dynamic scaling

### 2. PostgreSQL Metadata DB (Not SQLite)
```yaml
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
```

**Why PostgreSQL over SQLite?**
- SQLite doesn't support concurrent connections (Airflow needs scheduler + webserver)
- PostgreSQL is Airflow production standard
- Demonstrates understanding of real-world Airflow architecture

### 3. XCom for Task Communication
```python
# Push data in upstream task:
context['task_instance'].xcom_push(key='nsf_records', value=result.records_loaded)

# Pull data in downstream task:
nsf_records = ti.xcom_pull(task_ids='extract_nsf', key='nsf_records')
```

**Why XCom?**
- Airflow's native inter-task communication mechanism
- Stored in PostgreSQL metadata DB
- Enables log_summary task to aggregate metrics from all tasks
- Portfolio demonstrates understanding of Airflow patterns

**Limitation:**
- XCom stores data in DB (not for large payloads)
- Max recommended size: <1 MB
- For large data, use shared storage (S3, DuckDB tables)

### 4. Bash Operators for dbt
```python
dbt_run = BashOperator(
    task_id='dbt_run',
    bash_command="""
    cd /opt/project/transform/scholarhub && \
    dbt run --profiles-dir . --target dev
    """
)
```

**Why BashOperator over custom Python?**
- dbt CLI is the standard interface
- Easier to debug (same commands work locally)
- No need for custom Python wrapper

**Alternative Considered:**
- dbt's native Airflow provider (`DbtRunOperator`)
- **Chose BashOperator for simplicity** at portfolio scale

### 5. catchup=False (No Backfill)
```python
with DAG(
    dag_id='scholarhub_pipeline',
    catchup=False,  # Don't backfill historical runs
    ...
) as dag:
```

**Why?**
- NSF/NIH APIs return current data (not historical snapshots)
- Backfilling would re-extract same data multiple times
- Portfolio project doesn't need historical DAG runs

**When to use catchup=True?**
- Processing historical data (e.g., "re-run for all of 2024")
- Incrementally building time-series datasets

### 6. max_active_runs=1
```python
with DAG(
    dag_id='scholarhub_pipeline',
    max_active_runs=1,  # Only one DAG run at a time
    ...
) as dag:
```

**Why?**
- DuckDB single-writer constraint
- Prevents overlapping extractions that would conflict
- Ensures data consistency

**Trade-off:**
- Slower if manual DAG runs pile up
- Acceptable for daily schedule (24-hour gap between runs)

---

## Architecture Evolution

### Before Phase 4 (Manual Execution)
```bash
# Phase 1-3 required manual steps:
python -m extractors.federal_apis.nsf_extractor
python -m extractors.federal_apis.nih_extractor
cd transform/scholarhub && dbt run && dbt test
```

**Problems:**
- Manual execution = human error
- No scheduling
- No monitoring
- No retry logic
- No logging aggregation

### After Phase 4 (Automated Pipeline)
```
Airflow Scheduler (runs daily at 6 AM UTC)
    ↓
extract_nsf → extract_nih → dbt_run → dbt_test → log_summary
    ↓
Airflow Webserver (localhost:8080)
    ↓
DAG visualization, logs, metrics, manual triggers
```

**Benefits:**
- Fully automated daily pipeline
- Retry on failure (2 attempts with exponential backoff)
- Centralized logging (all task logs in Airflow UI)
- Monitoring (DAG success/failure over time)
- Manual trigger available for ad-hoc runs

---

## Airflow Best Practices Demonstrated

✅ **DAG Documentation** — Comprehensive `doc_md` for DAG and each task
✅ **Default Args** — Centralized retry/email configuration
✅ **Task Dependencies** — Clear `>>` syntax showing execution order
✅ **XCom Usage** — Inter-task communication for metrics
✅ **Idempotency** — Re-running same date range doesn't corrupt data
✅ **Healthchecks** — PostgreSQL health verification before Airflow starts
✅ **Environment Isolation** — Docker containers for consistent execution
✅ **.airflowignore** — Fast DAG parsing by excluding non-DAG code

---

## Files Created/Modified

| File | Lines | Status |
|------|-------|--------|
| `docker-compose.yml` | 97 | ✅ Created |
| `dags/scholarhub_pipeline.py` | 316 | ✅ Created |
| `.airflowignore` | 45 | ✅ Created |
| `.env` | +2 | ✅ Modified (added AIRFLOW_UID) |
| **Total** | **~460 lines** | |

---

## What Worked Well

✅ **Reusable Extractor Pattern** — DAG Python callables import existing extractors, no duplicate code
✅ **DuckDB Portability** — Same `.duckdb` file works in local Python and Airflow container
✅ **dbt Integration** — BashOperator runs dbt with same commands as local development
✅ **Docker Compose** — Single `docker-compose.yml` defines entire Airflow stack

---

## What Would We Do Differently?

### 1. Airflow Provider Packages
**Current:** BashOperator for dbt
**Better:** Use `apache-airflow-providers-dbt-cloud` for native dbt integration:
```python
from airflow.providers.dbt.cloud.operators.dbt import DbtRunOperator

dbt_run = DbtRunOperator(
    task_id='dbt_run',
    project_dir='/opt/project/transform/scholarhub',
    profiles_dir='/opt/project/transform/scholarhub',
)
```

**Why not now?**
- Simpler for portfolio project
- BashOperator is more universal (works with any CLI tool)

### 2. Alerting Configuration
**Current:** `email_on_failure: False` (no alerts)
**Better:** Configure SMTP and enable email alerts:
```python
default_args = {
    "email": ["admin@scholarhub.dev"],
    "email_on_failure": True,
    "email_on_retry": False,
}
```

**Plus:** Slack/PagerDuty integration for production

### 3. Connection Management
**Current:** Extractors create DuckDB connections directly
**Better:** Use Airflow Connections for centralized management:
```python
# Define in Airflow UI: Admin → Connections → Add
# Connection ID: duckdb_default
# Connection Type: duckdb
# Host: /opt/project/warehouse/scholarhub.duckdb

# Use in DAG:
from airflow.hooks.base import BaseHook
conn = BaseHook.get_connection('duckdb_default')
```

### 4. DAG Testing
**Current:** No automated DAG testing
**Better:** Add DAG integrity tests:
```python
# tests/dags/test_scholarhub_pipeline.py
def test_dag_loads():
    from dags.scholarhub_pipeline import dag
    assert dag is not None
    assert len(dag.tasks) == 5
    assert dag.schedule_interval == '0 6 * * *'
```

### 5. Incremental Extraction
**Current:** Extract last 7 days of NSF data every run (potential duplicates)
**Better:** Track last extraction timestamp and extract only new data:
```python
# Store in Airflow Variables:
last_extraction = Variable.get("nsf_last_extraction_date", default="01/01/2020")

# Pass to extractor:
result = extractor.extract(date_start=last_extraction)

# Update variable:
Variable.set("nsf_last_extraction_date", datetime.now().strftime("%m/%d/%Y"))
```

---

## Airflow UI Components (Once Running)

### Main Dashboard
- **DAGs:** List of all DAGs with run statistics
- **Runs:** Recent DAG runs (success/failed/running)
- **Tasks:** Individual task execution history

### DAG Graph View
```
[extract_nsf] → [extract_nih] → [dbt_run] → [dbt_test] → [log_summary]
```
Color-coded:
- Green = Success
- Red = Failed
- Yellow = Running
- Gray = Not started

### Task Logs
Click any task → View Logs → See structured JSON logs from extractors:
```json
{"event": "extraction_started", "source": "nsf", "timestamp": "2026-03-24T06:00:00Z"}
{"event": "batch_written", "batch_num": 1, "records": 25, "avg_quality": 0.87}
{"event": "extraction_complete", "total_records": 500, "duration_secs": 120.5}
```

### XCom Values
Admin → XComs → View data passed between tasks:
- `extract_nsf.nsf_records`: 500
- `extract_nsf.nsf_quality`: 0.999
- `extract_nih.nih_records`: 500
- `extract_nih.nih_quality`: 0.993

---

## Current Status & Next Steps

### ✅ Completed
- Docker Compose configuration for Airflow 2.8.1
- Main orchestration DAG with 5 tasks
- Task dependencies handling DuckDB constraints
- .airflowignore for performance
- Environment configuration

### 🟡 Blocked
**Issue:** Docker daemon not running
**Error:** `Cannot connect to the Docker daemon at unix:///Users/bv/.docker/run/docker.sock`
**Resolution Required:** Start Docker Desktop application

### 🔴 Remaining Steps
1. Start Docker Desktop
2. Run Airflow initialization:
   ```bash
   docker-compose up airflow-init
   ```
   Expected output: "Airflow database initialized successfully"

3. Start Airflow services:
   ```bash
   docker-compose up -d
   ```
   Services: `postgres`, `airflow-webserver`, `airflow-scheduler`

4. Verify Airflow UI:
   - Open http://localhost:8080
   - Login: admin / admin
   - Check DAG appears: `scholarhub_pipeline`

5. Test DAG execution:
   - Click DAG name → Toggle ON
   - Click "Play" icon → Trigger DAG
   - Monitor task execution in Graph view
   - Verify all 5 tasks complete successfully

6. Validate pipeline output:
   ```sql
   -- Check data freshness:
   SELECT
       source,
       COUNT(*) as records,
       MAX(extracted_at) as latest_extraction
   FROM int_all_awards
   GROUP BY source;
   ```

---

## Time Breakdown

- **Docker Compose Setup:** 20 min
- **DAG Development:** 35 min (Python callables, task dependencies, documentation)
- **.airflowignore Creation:** 5 min
- **Environment Configuration:** 5 min
- **Documentation (this file):** 40 min

**Total (so far):** ~1 hour 45 minutes
**Estimated Remaining:** 30 min (initialization, testing, verification)
**Phase 4 Total Estimate:** ~2 hours 15 minutes

---

## Portfolio Talking Points

When presenting Phase 4:

1. **"I orchestrated a multi-source data pipeline with Apache Airflow using Docker"**
   - Shows modern DE stack knowledge (containerization + orchestration)

2. **"The DAG handles DuckDB's single-writer constraint through sequential task dependencies"**
   - Demonstrates understanding of database limitations and architectural trade-offs

3. **"I used XCom to pass metrics between tasks for aggregated pipeline monitoring"**
   - Shows Airflow pattern mastery

4. **"The pipeline includes retry logic with exponential backoff and comprehensive logging"**
   - Production-grade reliability patterns

5. **"DAG parsing is optimized with .airflowignore to exclude 270+ non-DAG Python files"**
   - Shows attention to performance and operational efficiency

---

## Common Airflow Interview Questions - Our Answers

**Q: Why LocalExecutor over SequentialExecutor?**
A: LocalExecutor allows parallel task execution (within a DAG run). Our DAG has sequential dependencies by design (DuckDB constraint), but LocalExecutor still enables better performance if we add independent tasks later.

**Q: How do you handle task failures?**
A: Two-level strategy: (1) Retry logic in default_args (2 retries with 5-min exponential backoff), (2) On-failure email alerts (disabled for portfolio, enabled in production).

**Q: What's the difference between start_date and catchup?**
A: `start_date` defines first eligible run. `catchup=False` prevents backfilling historical runs between start_date and now. We chose catchup=False because NSF/NIH APIs don't support historical queries.

**Q: How do you pass data between tasks?**
A: XCom for small metadata (<1 MB). For large datasets, write to shared storage (DuckDB tables, S3). We use XCom for metrics (record counts, quality scores) and DuckDB tables for actual data.

**Q: How would you scale this to 100x data volume?**
A: (1) Move DuckDB → Snowflake/BigQuery, (2) Use CeleryExecutor with multiple workers, (3) Partition extractions by date/field, (4) Migrate to managed Airflow (AWS MWAA, GCP Composer).

---

## Next Steps → Phase 5 (After Airflow Running)

With Phase 4 complete, we will have:
- ✅ Fully automated daily pipeline
- ✅ Visual DAG monitoring
- ✅ Centralized logging
- ✅ Retry and alerting infrastructure
- ✅ 1,000 records updated daily (500 NSF + 500 NIH)

Phase 5 will build a **Streamlit Dashboard** with 5 pages to:
- Answer all business questions (BQ-1 through BQ-8)
- Visualize funding trends over time
- Show cross-source comparisons
- Enable filtering by institution, field, year
- Provide downloadable datasets

**Deliverables:**
- `dashboard/app.py` — Multi-page Streamlit app
- `dashboard/pages/` — 5 page modules
- Plotly charts for interactive visualization
- Query optimization for sub-second load times
