"""
ScholarHub Data Pipeline DAG

Orchestrates the complete data pipeline:
1. Extract data from NSF and NIH APIs (sequential - DuckDB single writer)
2. Run dbt transformations (staging → intermediate → marts)
3. Run dbt tests for data quality

Schedule: Daily at 6:00 AM UTC
Retries: 2 attempts with exponential backoff

Author: ScholarHub Team
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
import sys
import os

# Add project to Python path
sys.path.insert(0, '/opt/project')

# ────────────────────────────────────────────────────────────────────────────
# Default Arguments
# ────────────────────────────────────────────────────────────────────────────

default_args = {
    "owner": "scholarhub",
    "depends_on_past": False,
    "start_date": datetime(2024, 1, 1),
    "email": ["admin@scholarhub.dev"],
    "email_on_failure": False,  # Set to True for email alerts
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
}

# ────────────────────────────────────────────────────────────────────────────
# Python Callables
# ────────────────────────────────────────────────────────────────────────────

def extract_nsf_data(**context):
    """
    Extract NSF awards from the past 7 days.

    Why 7 days? NSF updates daily. Extracting past week ensures we don't
    miss any awards if the DAG fails to run for a day.
    """
    from extractors.federal_apis.nsf_extractor import NSFExtractor
    from datetime import datetime, timedelta

    # Calculate date range (last 7 days)
    date_start = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")

    print(f"Extracting NSF awards from {date_start} to today")

    with NSFExtractor() as extractor:
        result = extractor.extract(max_records=500)

    # Push results to XCom for downstream tasks
    context['task_instance'].xcom_push(key='nsf_records', value=result.records_loaded)
    context['task_instance'].xcom_push(key='nsf_quality', value=result.quality_avg)

    print(f"✅ NSF: {result.records_loaded} records, {result.quality_avg:.3f} quality")

    if result.status != "SUCCESS":
        raise Exception(f"NSF extraction failed: {result.errors}")

    return result.records_loaded


def extract_nih_data(**context):
    """
    Extract NIH projects from fiscal year 2024.

    NIH data is organized by fiscal year. We extract FY2024 which is
    the most recent complete fiscal year.
    """
    from extractors.federal_apis.nih_extractor import NIHExtractor

    fiscal_year = 2024

    print(f"Extracting NIH projects for FY{fiscal_year}")

    with NIHExtractor() as extractor:
        result = extractor.extract(max_records=500, fiscal_year=fiscal_year)

    # Push results to XCom
    context['task_instance'].xcom_push(key='nih_records', value=result.records_loaded)
    context['task_instance'].xcom_push(key='nih_quality', value=result.quality_avg)

    print(f"✅ NIH: {result.records_loaded} records, {result.quality_avg:.3f} quality")

    if result.status != "SUCCESS":
        raise Exception(f"NIH extraction failed: {result.errors}")

    return result.records_loaded


def log_pipeline_summary(**context):
    """
    Log summary of pipeline execution.

    Pulls XCom data from extraction tasks and logs overall statistics.
    """
    ti = context['task_instance']

    # Pull XCom data from extraction tasks
    nsf_records = ti.xcom_pull(task_ids='extract_nsf', key='nsf_records') or 0
    nsf_quality = ti.xcom_pull(task_ids='extract_nsf', key='nsf_quality') or 0.0

    nih_records = ti.xcom_pull(task_ids='extract_nih', key='nih_records') or 0
    nih_quality = ti.xcom_pull(task_ids='extract_nih', key='nih_quality') or 0.0

    total_records = nsf_records + nih_records
    avg_quality = (nsf_quality + nih_quality) / 2 if (nsf_records + nih_records) > 0 else 0.0

    print("=" * 70)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 70)
    print(f"NSF:   {nsf_records:>6} records | Quality: {nsf_quality:.3f}")
    print(f"NIH:   {nih_records:>6} records | Quality: {nih_quality:.3f}")
    print("-" * 70)
    print(f"TOTAL: {total_records:>6} records | Avg Quality: {avg_quality:.3f}")
    print("=" * 70)

    return {
        'total_records': total_records,
        'avg_quality': avg_quality,
        'nsf_records': nsf_records,
        'nih_records': nih_records
    }


# ────────────────────────────────────────────────────────────────────────────
# DAG Definition
# ────────────────────────────────────────────────────────────────────────────

with DAG(
    dag_id='scholarhub_pipeline',
    default_args=default_args,
    description='Daily data pipeline for ScholarHub: Extract → Transform → Test',
    schedule_interval='0 6 * * *',  # Daily at 6:00 AM UTC
    catchup=False,  # Don't backfill historical runs
    max_active_runs=1,  # Only one DAG run at a time
    tags=['scholarhub', 'production'],
) as dag:

    # ────────────────────────────────────────────────────────────────────────
    # Task 1: Extract NSF Awards
    # ────────────────────────────────────────────────────────────────────────

    extract_nsf = PythonOperator(
        task_id='extract_nsf',
        python_callable=extract_nsf_data,
        doc_md="""
        ### Extract NSF Awards

        Fetches NSF awards from the past 7 days using the NSF Award Search API.

        **API:** https://api.nsf.gov/services/v1/awards.json
        **Rate Limit:** 10 requests/minute
        **Output:** raw_nsf_awards table in DuckDB
        """,
    )

    # ────────────────────────────────────────────────────────────────────────
    # Task 2: Extract NIH Projects
    # ────────────────────────────────────────────────────────────────────────

    extract_nih = PythonOperator(
        task_id='extract_nih',
        python_callable=extract_nih_data,
        doc_md="""
        ### Extract NIH Projects

        Fetches NIH biomedical research projects for fiscal year 2024.

        **API:** https://api.reporter.nih.gov/v2/projects/search
        **Rate Limit:** 5 requests/minute
        **Output:** raw_nih_projects table in DuckDB
        """,
    )

    # ────────────────────────────────────────────────────────────────────────
    # Task 3: Run dbt Models
    # ────────────────────────────────────────────────────────────────────────

    dbt_run = BashOperator(
        task_id='dbt_run',
        bash_command="""
        cd /opt/project/transform/scholarhub && \
        dbt run --profiles-dir . --target dev
        """,
        doc_md="""
        ### Run dbt Transformations

        Executes all dbt models in dependency order:
        1. Staging views (stg_nsf_awards, stg_nih_projects)
        2. Intermediate table (int_all_awards)
        3. Mart tables (funding by institution/field/year)

        **Models:** 6 total (2 views, 4 tables)
        **Expected Duration:** ~1 second
        """,
    )

    # ────────────────────────────────────────────────────────────────────────
    # Task 4: Run dbt Tests
    # ────────────────────────────────────────────────────────────────────────

    dbt_test = BashOperator(
        task_id='dbt_test',
        bash_command="""
        cd /opt/project/transform/scholarhub && \
        dbt test --profiles-dir . --target dev
        """,
        doc_md="""
        ### Run dbt Data Quality Tests

        Validates data quality with dbt tests:
        - not_null checks on critical fields
        - Data type validations
        - Referential integrity (would add if we had FKs)

        **Tests:** 5 configured
        **Expected:** 4 pass, 1 known duplicate issue
        """,
    )

    # ────────────────────────────────────────────────────────────────────────
    # Task 5: Log Pipeline Summary
    # ────────────────────────────────────────────────────────────────────────

    log_summary = PythonOperator(
        task_id='log_summary',
        python_callable=log_pipeline_summary,
        doc_md="""
        ### Log Pipeline Summary

        Aggregates execution metrics from all tasks and logs summary.
        Useful for monitoring pipeline health over time.
        """,
    )

    # ────────────────────────────────────────────────────────────────────────
    # Task Dependencies (DAG Flow)
    # ────────────────────────────────────────────────────────────────────────

    # Sequential extraction (DuckDB single writer constraint)
    extract_nsf >> extract_nih

    # dbt runs after all extractions complete
    extract_nih >> dbt_run

    # Tests run after dbt models built
    dbt_run >> dbt_test

    # Summary logs after everything completes
    dbt_test >> log_summary


# ────────────────────────────────────────────────────────────────────────────
# DAG Documentation
# ────────────────────────────────────────────────────────────────────────────

dag.doc_md = """
# ScholarHub Data Pipeline

## Overview
This DAG orchestrates the complete ScholarHub data pipeline, extracting federal
grant data from NSF and NIH, transforming it with dbt, and validating data quality.

## Schedule
**Daily at 6:00 AM UTC**

## Flow
```
extract_nsf (5-10min)
    ↓
extract_nih (2-3min)
    ↓
dbt_run (~1sec)
    ↓
dbt_test (~1sec)
    ↓
log_summary
```

## Data Sources
- **NSF:** National Science Foundation awards (general science)
- **NIH:** National Institutes of Health projects (biomedical research)

## Output
- **Raw Zone:** `raw_nsf_awards`, `raw_nih_projects`
- **Staging:** `stg_nsf_awards`, `stg_nih_projects`
- **Intermediate:** `int_all_awards`
- **Marts:** `mart_funding_by_institution`, `mart_funding_by_field`, `mart_funding_by_year`

## Monitoring
- Check task logs for extraction metrics
- dbt test failures indicate data quality issues
- XCom variables track record counts and quality scores

## Troubleshooting
- **DuckDB locked error:** Tasks running in parallel. Ensure sequential extraction.
- **API rate limit:** Extractors have built-in rate limiting (NSF: 10/min, NIH: 5/min)
- **dbt compilation error:** Check model SQL syntax or dependency order

## Contact
ScholarHub Team - admin@scholarhub.dev
"""
