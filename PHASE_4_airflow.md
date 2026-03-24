# Phase 4 — Airflow Orchestration

**Goal:** Every step that was run manually in Phases 1–3 now runs automatically on schedule. One command starts the whole system. Failed tasks retry automatically. You have a web UI showing pipeline status.

**Duration:** ~1 week  
**Prerequisite:** Phases 1–3 complete and working manually.

---

## The Mental Model: Why Airflow?

The question isn't "why not cron?" — cron can technically run scripts on schedule. The question is: what happens when something fails?

With cron:
- Job fails at 2 AM
- You wake up at 9 AM to a broken dashboard
- No way to see which step failed or why
- Re-running is manual, order-dependent

With Airflow:
- Job fails at 2 AM
- Airflow retries automatically (3 attempts with backoff)
- You get an email/alert
- You open the web UI and see exactly which task failed and the full logs
- You fix the issue and click "Clear Task" to re-run from the exact failure point
- Backfill runs all missed executions automatically

For portfolio: the DAG graph in Airflow UI is visual proof that you understand pipeline dependencies. Screenshots of a green DAG are worth including in a portfolio.

---

## Step 4.1 — Docker Compose Setup

Airflow has a complex setup (webserver, scheduler, worker, metadata DB). Docker Compose handles all of this.

Create `docker-compose.yml` at project root:

```yaml
# docker-compose.yml
# Airflow 2.8 with LocalExecutor (single machine, no distributed workers)
# Perfect for a portfolio project — same patterns as production, smaller scale.

version: '3.8'

x-airflow-common: &airflow-common
  image: apache/airflow:2.8.1-python3.11
  environment: &airflow-common-env
    AIRFLOW__CORE__EXECUTOR: LocalExecutor
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:airflow@postgres/airflow
    AIRFLOW__CORE__FERNET_KEY: ''
    AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'true'
    AIRFLOW__CORE__LOAD_EXAMPLES: 'false'
    AIRFLOW__API__AUTH_BACKENDS: 'airflow.api.auth.backend.basic_auth,airflow.api.auth.backend.session'
    # Mount project code into Airflow
    PYTHONPATH: '/opt/project'
    # Pass through environment variables from .env
    DUCKDB_PATH: '/opt/project/warehouse/scholarhub.duckdb'
    NSF_API_RATE_LIMIT: '10'
    NIH_API_RATE_LIMIT: '5'
  volumes:
    - .:/opt/project           # Mount entire project
    - ./dags:/opt/airflow/dags # Airflow looks here for DAGs
    - ./logs:/opt/airflow/logs
    - ./plugins:/opt/airflow/plugins
  user: "${AIRFLOW_UID:-50000}:0"
  depends_on: &airflow-common-depends-on
    postgres:
      condition: service_healthy

services:
  postgres:
    image: postgres:13
    environment:
      POSTGRES_USER: airflow
      POSTGRES_PASSWORD: airflow
      POSTGRES_DB: airflow
    volumes:
      - postgres-db-volume:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "airflow"]
      interval: 5s
      retries: 5

  airflow-webserver:
    <<: *airflow-common
    command: webserver
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "--fail", "http://localhost:8080/health"]
      interval: 10s
      timeout: 10s
      retries: 5

  airflow-scheduler:
    <<: *airflow-common
    command: scheduler
    healthcheck:
      test: ["CMD-SHELL", 'airflow jobs check --job-type SchedulerJob --hostname "$${HOSTNAME}"']
      interval: 10s
      timeout: 10s
      retries: 5

  airflow-init:
    <<: *airflow-common
    entrypoint: /bin/bash
    command:
      - -c
      - |
        airflow db init
        airflow users create \
          --username admin \
          --firstname Admin \
          --lastname User \
          --role Admin \
          --email admin@example.com \
          --password admin
    environment:
      <<: *airflow-common-env
      _AIRFLOW_DB_UPGRADE: 'true'

volumes:
  postgres-db-volume:
```

Start Airflow:
```bash
# Create required directories
mkdir -p dags logs plugins

# Set Airflow UID (required on Linux)
echo "AIRFLOW_UID=$(id -u)" >> .env

# Initialize and start
docker-compose up airflow-init
docker-compose up -d

# Wait ~30 seconds, then open: http://localhost:8080
# Login: admin / admin
```

---

## Step 4.2 — Main Pipeline DAG

Create `dags/scholarhub_pipeline.py`:

```python
# dags/scholarhub_pipeline.py
"""
Main ScholarHub data pipeline DAG.

Runs daily at 6:00 AM UTC.
Flow: Extract (parallel) → dbt staging → dbt intermediate → dbt marts → dbt tests

Task naming convention:
  extract_{source}     = Pull data from source into raw zone
  dbt_{layer}_{model}  = Run specific dbt model
  dbt_test_{layer}     = Run dbt tests for a layer

Why LocalExecutor with sequential tasks?
DuckDB only allows one writer at a time. If extract_nsf and extract_nih
run truly in parallel, they'll both try to write to the same .duckdb file
and one will fail with "database is locked".
Solution: Extract tasks run sequentially (one after another), then
dbt runs after all extracts complete.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.utils.email import send_email
import sys
import os

# Add project to Python path
sys.path.insert(0, '/opt/project')

# ── Default args for all tasks ───────────────────────────────────────────────
default_args = {
    "owner": "scholarhub",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 1),
    "email": [],  # Add your email here for failure alerts
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,  # 5min, 10min between retries
}

# ── Python callables (imported lazily to avoid import errors at parse time) ──

def run_nsf_extract(**context):
    """Extract last 7 days of NSF awards."""
    from datetime import datetime, timedelta
    from extractors.federal_apis.nsf_extractor import NSFExtractor

    extractor = NSFExtractor()
    date_start = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")
    result = extractor.extract(
        keyword="graduate fellowship doctoral training",
        date_start=date_start,
    )

    # Push result to XCom for downstream tasks to read
    context['ti'].xcom_push(key='nsf_result', value={
        'status': result.status,
        'records_loaded': result.records_loaded,
        'quality_avg': result.quality_avg,
    })

    if result.status == 'failed':
        raise ValueError(f"NSF extract failed: {result.errors}")

    return result.records_loaded


def run_nih_extract(**context):
    """Extract NIH training and research grants (current fiscal year)."""
    import datetime as dt
    from extractors.federal_apis.nih_extractor import NIHExtractor

    extractor = NIHExtractor()
    current_year = dt.datetime.now().year

    result = extractor.extract(
        fiscal_years=[current_year - 1, current_year],
        activity_codes=["T32", "R01", "R21", "F31", "F32"],
    )

    context['ti'].xcom_push(key='nih_result', value={
        'status': result.status,
        'records_loaded': result.records_loaded,
        'quality_avg': result.quality_avg,
    })

    if result.status == 'failed':
        raise ValueError(f"NIH extract failed: {result.errors}")

    return result.records_loaded


def run_nserc_extract(**context):
    """
    NSERC is a bulk CSV download — only run this weekly (not daily).
    For daily runs, this task is skipped if the CSV is fresh (< 7 days old).
    """
    from pathlib import Path
    import os
    from extractors.federal_apis.nserc_extractor import NSERCExtractor

    csv_path = Path("/opt/project/data/raw/nserc/nserc_awards_latest.csv")

    if not csv_path.exists():
        # File doesn't exist — this is a soft failure, log and continue
        context['ti'].xcom_push(key='nserc_result', value={'status': 'skipped'})
        print("NSERC CSV not found, skipping. Download manually and place at:")
        print(str(csv_path))
        return 0

    # Check file age
    file_age_days = (datetime.now().timestamp() - csv_path.stat().st_mtime) / 86400
    if file_age_days > 7:
        print(f"NSERC CSV is {file_age_days:.0f} days old. Consider refreshing.")

    extractor = NSERCExtractor()
    result = extractor.extract(str(csv_path))

    context['ti'].xcom_push(key='nserc_result', value={
        'status': result.status,
        'records_loaded': result.records_loaded,
    })

    return result.records_loaded


def run_pipeline_health_check(**context):
    """
    Verify that critical tables are fresh and have expected row counts.
    Fails the task (and triggers alert) if data is stale.
    """
    import duckdb
    conn = duckdb.connect('/opt/project/warehouse/scholarhub.duckdb', read_only=True)

    checks = []

    # Check 1: mart_funding_by_field has rows
    count = conn.execute(
        "SELECT COUNT(*) FROM analytics_marts.mart_funding_by_field"
    ).fetchone()[0]
    checks.append(('mart_funding_by_field row count', count > 0, count))

    # Check 2: raw_crawl_log has a recent entry
    recent = conn.execute("""
        SELECT COUNT(*) FROM main.raw_crawl_log
        WHERE crawled_at >= NOW() - INTERVAL '25 hours'
    """).fetchone()[0]
    checks.append(('recent crawl log entries', recent > 0, recent))

    # Check 3: NSF raw data is growing
    nsf_count = conn.execute("SELECT COUNT(*) FROM main.raw_nsf_awards").fetchone()[0]
    checks.append(('NSF raw records', nsf_count > 100, nsf_count))

    conn.close()

    # Log all checks
    for check_name, passed, value in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {check_name}: {value}")

    failed = [c for c in checks if not c[1]]
    if failed:
        raise ValueError(f"Health check failed: {[c[0] for c in failed]}")

    return "All health checks passed"


# ── DAG Definition ─────────────────────────────────────────────────────────
with DAG(
    dag_id="scholarhub_daily_pipeline",
    default_args=default_args,
    description="Daily ScholarHub data pipeline: Extract → Transform → Test → Validate",
    schedule_interval="0 6 * * *",   # 6:00 AM UTC every day
    catchup=False,                   # Don't run missed executions on startup
    max_active_runs=1,               # Only one run at a time (DuckDB single-writer)
    tags=["scholarhub", "production"],
) as dag:

    # ── EXTRACT LAYER (Sequential due to DuckDB single-writer) ──────────────

    extract_nsf = PythonOperator(
        task_id="extract_nsf_awards",
        python_callable=run_nsf_extract,
        execution_timeout=timedelta(minutes=30),
        doc_md="Pull last 7 days of NSF awards into raw_nsf_awards",
    )

    extract_nih = PythonOperator(
        task_id="extract_nih_projects",
        python_callable=run_nih_extract,
        execution_timeout=timedelta(minutes=45),
        doc_md="Pull NIH T32/R01/F31 projects for current fiscal year",
    )

    extract_nserc = PythonOperator(
        task_id="extract_nserc_awards",
        python_callable=run_nserc_extract,
        execution_timeout=timedelta(minutes=10),
        doc_md="Load NSERC CSV if available and fresh",
    )

    # ── TRANSFORM LAYER (dbt) ───────────────────────────────────────────────
    # Run dbt inside Docker — same Python environment, same filesystem

    dbt_staging = BashOperator(
        task_id="dbt_run_staging",
        bash_command=(
            "cd /opt/project/transform/scholarhub && "
            "dbt run --select staging.* --profiles-dir . "
            "--no-use-colors 2>&1"
        ),
        execution_timeout=timedelta(minutes=10),
        doc_md="Build all staging views from raw tables",
    )

    dbt_intermediate = BashOperator(
        task_id="dbt_run_intermediate",
        bash_command=(
            "cd /opt/project/transform/scholarhub && "
            "dbt run --select intermediate.* --profiles-dir . "
            "--no-use-colors 2>&1"
        ),
        execution_timeout=timedelta(minutes=10),
    )

    dbt_marts = BashOperator(
        task_id="dbt_run_marts",
        bash_command=(
            "cd /opt/project/transform/scholarhub && "
            "dbt run --select marts.* dimensions.* --profiles-dir . "
            "--no-use-colors 2>&1"
        ),
        execution_timeout=timedelta(minutes=15),
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=(
            "cd /opt/project/transform/scholarhub && "
            "dbt test --profiles-dir . "
            "--no-use-colors 2>&1"
        ),
        execution_timeout=timedelta(minutes=10),
        # Don't fail the whole DAG if tests have warnings
        # Change to True if you want strict test enforcement
    )

    # ── VALIDATION LAYER ────────────────────────────────────────────────────

    health_check = PythonOperator(
        task_id="pipeline_health_check",
        python_callable=run_pipeline_health_check,
        execution_timeout=timedelta(minutes=5),
        doc_md="Verify critical tables are fresh and have expected data",
    )

    # ── DEPENDENCIES ────────────────────────────────────────────────────────
    # Extract tasks run sequentially (DuckDB single-writer constraint)
    # dbt runs after all extracts complete

    extract_nsf >> extract_nih >> extract_nserc
    extract_nserc >> dbt_staging >> dbt_intermediate >> dbt_marts >> dbt_test >> health_check
```

---

## Step 4.3 — Backfill DAG

Create `dags/scholarhub_backfill.py`:

```python
# dags/scholarhub_backfill.py
"""
One-time historical backfill DAG.
Run this ONCE to load historical NSF data from 2015 onwards.
Then trigger the main pipeline for ongoing updates.

To trigger manually from Airflow UI:
  DAGs → scholarhub_backfill → Trigger DAG
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
sys.path.insert(0, '/opt/project')

default_args = {
    "owner": "scholarhub",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "start_date": datetime(2024, 1, 1),
}


def backfill_nsf_historical(**context):
    """
    Load NSF awards from 2015 to today.
    This can take 30-60 minutes depending on the keyword scope.
    Run once during project setup.
    """
    from extractors.federal_apis.nsf_extractor import NSFExtractor
    import datetime as dt

    extractor = NSFExtractor()
    total_loaded = 0

    # Load year by year to stay within API timeout limits
    for year in range(2015, dt.datetime.now().year + 1):
        result = extractor.extract(
            keyword="graduate fellowship doctoral training research",
            date_start=f"01/01/{year}",
            max_pages=20,  # ~500 awards per year is sufficient for analysis
        )
        total_loaded += result.records_loaded
        print(f"Year {year}: loaded {result.records_loaded} awards (total: {total_loaded})")

    return total_loaded


def backfill_nih_historical(**context):
    """Load NIH training grants from 2015-2024."""
    from extractors.federal_apis.nih_extractor import NIHExtractor

    extractor = NIHExtractor()
    result = extractor.extract(
        fiscal_years=list(range(2015, 2025)),
        activity_codes=["T32", "F31", "R01"],
        max_records=50000,
    )
    return result.records_loaded


with DAG(
    dag_id="scholarhub_backfill",
    default_args=default_args,
    description="One-time historical data load (run once during setup)",
    schedule_interval=None,  # Manual trigger only
    catchup=False,
    tags=["scholarhub", "setup"],
) as dag:

    nsf_backfill = PythonOperator(
        task_id="backfill_nsf_2015_to_present",
        python_callable=backfill_nsf_historical,
        execution_timeout=timedelta(hours=2),
    )

    nih_backfill = PythonOperator(
        task_id="backfill_nih_2015_to_present",
        python_callable=backfill_nih_historical,
        execution_timeout=timedelta(hours=2),
    )

    dbt_full_refresh = BashOperator(
        task_id="dbt_full_refresh",
        bash_command=(
            "cd /opt/project/transform/scholarhub && "
            "dbt run --full-refresh --profiles-dir . 2>&1"
        ),
        execution_timeout=timedelta(minutes=30),
    )

    # Run in parallel since backfills don't conflict in terms of tables
    [nsf_backfill, nih_backfill] >> dbt_full_refresh
```

---

## Step 4.4 — Verify Airflow Setup

```bash
# Check all services are running
docker-compose ps

# Should show: all containers "Up" or "healthy"

# Check Airflow can find your DAGs
docker-compose exec airflow-scheduler airflow dags list

# Expected output:
# dag_id                      | filepath                        | owner
# scholarhub_daily_pipeline   | /opt/airflow/dags/scholarhub... | scholarhub
# scholarhub_backfill         | /opt/airflow/dags/scholarhub... | scholarhub

# Trigger the backfill manually (first time setup)
docker-compose exec airflow-scheduler airflow dags trigger scholarhub_backfill

# Watch it run
docker-compose exec airflow-scheduler airflow tasks logs scholarhub_backfill backfill_nsf_2015_to_present
```

Open `http://localhost:8080`:
- Login: admin / admin
- Go to DAGs → scholarhub_daily_pipeline
- Click the play button to trigger a test run
- Click on the run to see the task graph

---

## Step 4.5 — Makefile for Common Operations

Create `Makefile` at project root:

```makefile
# Makefile — common operations

.PHONY: init extract transform test dashboard airflow-up airflow-down

# Setup
init:
	python warehouse/init_warehouse.py
	cd transform/scholarhub && dbt deps

# Extraction (manual, for development)
extract-nsf:
	python -c "from extractors.federal_apis.nsf_extractor import NSFExtractor; print(NSFExtractor().extract(max_pages=3))"

extract-nih:
	python -c "from extractors.federal_apis.nih_extractor import NIHExtractor; print(NIHExtractor().extract(max_records=1000))"

# dbt transforms
transform:
	cd transform/scholarhub && dbt run --profiles-dir .

transform-full:
	cd transform/scholarhub && dbt run --full-refresh --profiles-dir .

# Tests
test-python:
	pytest tests/ -v

test-dbt:
	cd transform/scholarhub && dbt test --profiles-dir .

test:
	make test-python && make test-dbt

# Dashboard
dashboard:
	streamlit run dashboard/app.py

# Airflow
airflow-up:
	docker-compose up -d
	@echo "Airflow UI: http://localhost:8080 (admin/admin)"

airflow-down:
	docker-compose down

airflow-logs:
	docker-compose logs -f airflow-scheduler

# DuckDB interactive shell
db:
	duckdb warehouse/scholarhub.duckdb
```

Usage:
```bash
make init          # First-time setup
make extract-nsf   # Manual extract for testing
make transform     # Run dbt pipeline
make dashboard     # Launch Streamlit
make airflow-up    # Start Airflow
```

---

## Phase 4 Checklist

- [ ] `docker-compose up -d` starts without errors
- [ ] Airflow UI accessible at `http://localhost:8080`
- [ ] Both DAGs appear in the DAGs list
- [ ] Trigger `scholarhub_backfill` manually and it completes green
- [ ] Daily DAG runs on schedule (test by triggering manually)
- [ ] Failed task retry works (force a failure, watch it retry)
- [ ] `make test` passes all Python and dbt tests

**What you've built:**
- Fully automated pipeline that runs at 6 AM daily
- Visual DAG showing task dependencies (screenshot this for portfolio)
- Retry logic with exponential backoff
- Health check task that catches silent failures
- Historical backfill capability

**Next:** Phase 5 — Streamlit dashboard with 5 pages answering the core business questions.
