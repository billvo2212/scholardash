# Phase 1 — Foundations: DuckDB + NSF Extractor

**Goal:** By the end of this phase you have a working pipeline that pulls real NSF grant data from the API and stores it in DuckDB with proper raw/staging zones.

**Duration:** ~1 week  
**Prerequisite:** Python 3.11+, pip, git

---

## Step 1.1 — Project Setup

### 1. Create directory structure

```bash
mkdir scholarhub-de
cd scholarhub-de

mkdir -p extractors/federal_apis
mkdir -p extractors/scrapers
mkdir -p extractors/validators
mkdir -p extractors/utils
mkdir -p warehouse/schema
mkdir -p data/raw/nsf
mkdir -p data/raw/nih
mkdir -p data/raw/nserc
mkdir -p data/exports
mkdir -p tests/unit
mkdir -p tests/fixtures
mkdir -p config
mkdir -p docs

# Create __init__.py files
touch extractors/__init__.py
touch extractors/federal_apis/__init__.py
touch extractors/scrapers/__init__.py
touch extractors/validators/__init__.py
touch extractors/utils/__init__.py
touch tests/__init__.py
touch tests/unit/__init__.py
```

### 2. Create `pyproject.toml`

```toml
[tool.poetry]
name = "scholarhub-de"
version = "0.1.0"
description = "Graduate funding intelligence data pipeline"
authors = ["Your Name <you@example.com>"]

[tool.poetry.dependencies]
python = "^3.11"
duckdb = "^0.10.0"
requests = "^2.31.0"
python-dotenv = "^1.0.0"
pydantic-settings = "^2.2.0"
tenacity = "^8.2.3"        # retry logic
structlog = "^24.1.0"      # structured logging
tqdm = "^4.66.0"           # progress bars

[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"
pytest-cov = "^4.1.0"
black = "^24.0.0"
ruff = "^0.3.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

Install dependencies:
```bash
pip install poetry
poetry install
# OR if using pip directly:
pip install duckdb requests python-dotenv pydantic-settings tenacity structlog tqdm pytest
```

### 3. Create `.env.example`

```bash
# Copy to .env and fill in values
DUCKDB_PATH=warehouse/scholarhub.duckdb
NSF_API_RATE_LIMIT_PER_MINUTE=10
NIH_API_RATE_LIMIT_PER_MINUTE=5
LOG_LEVEL=INFO
```

```bash
cp .env.example .env
```

---

## Step 1.2 — Config Module

Create `config/settings.py`:

```python
# config/settings.py
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Warehouse
    duckdb_path: str = "warehouse/scholarhub.duckdb"

    # Rate limits (requests per minute)
    nsf_api_rate_limit: int = 10
    nih_api_rate_limit: int = 5

    # Paths
    raw_data_dir: Path = Path("data/raw")
    exports_dir: Path = Path("data/exports")

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Singleton — import this everywhere
settings = Settings()
```

---

## Step 1.3 — Utility Modules

### Logger — `extractors/utils/logger.py`

```python
# extractors/utils/logger.py
import structlog
import logging
from config.settings import settings


def get_logger(name: str):
    """
    Returns a structured logger.
    Why structlog? JSON-formatted logs are parseable by Airflow and log aggregators.
    Plain print() statements disappear into noise when you have 10 extractors running.
    """
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.log_level.upper()),
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    return structlog.get_logger(name)
```

### Database Connection — `extractors/utils/db_connection.py`

```python
# extractors/utils/db_connection.py
import duckdb
from pathlib import Path
from config.settings import settings
from extractors.utils.logger import get_logger

logger = get_logger(__name__)

_connection = None  # module-level singleton


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """
    Returns a DuckDB connection.

    CRITICAL WARNING: DuckDB only allows ONE writer at a time.
    If two processes try to write simultaneously, the second one fails.
    Solution: Use read_only=True for any process that only reads.
    In Airflow, serialize write tasks so they don't overlap.

    Why singleton? Opening/closing DuckDB connections has overhead.
    One connection per process is the right pattern.
    """
    global _connection

    db_path = Path(settings.duckdb_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if _connection is None:
        logger.info("opening_duckdb_connection", path=str(db_path), read_only=read_only)
        _connection = duckdb.connect(str(db_path), read_only=read_only)
        # Enable progress bar for long queries
        _connection.execute("SET enable_progress_bar = true")

    return _connection


def close_connection():
    """Call this at the end of a process / Airflow task."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("duckdb_connection_closed")
```

### Rate Limiter — `extractors/utils/rate_limiter.py`

```python
# extractors/utils/rate_limiter.py
import time
from collections import deque
from threading import Lock


class RateLimiter:
    """
    Token bucket rate limiter.

    Why not just time.sleep()? Because sleep(6) between every request
    means if a request takes 2 seconds, you still wait 6 more.
    Token bucket respects the actual elapsed time.

    Usage:
        limiter = RateLimiter(requests_per_minute=10)
        for item in items:
            limiter.wait()
            response = requests.get(url)
    """

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute
        self.timestamps: deque = deque()
        self.lock = Lock()

    def wait(self):
        """Block until it's safe to make the next request."""
        with self.lock:
            now = time.monotonic()

            # Remove timestamps older than 1 minute
            while self.timestamps and now - self.timestamps[0] > 60.0:
                self.timestamps.popleft()

            if len(self.timestamps) >= self.requests_per_minute:
                # Must wait until oldest request is > 1 minute ago
                sleep_time = 60.0 - (now - self.timestamps[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)

            self.timestamps.append(time.monotonic())
```

---

## Step 1.4 — Base Extractor

Create `extractors/base.py`:

```python
# extractors/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

from extractors.utils.db_connection import get_connection
from extractors.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExtractResult:
    """Returned by every extractor's extract() method."""
    source_name: str
    records_found: int
    records_loaded: int
    records_failed: int
    quality_avg: float
    duration_secs: float
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.records_failed == 0:
            return "success"
        if self.records_loaded > 0:
            return "partial"
        return "failed"


class BaseExtractor(ABC):
    """
    Abstract base class for all extractors.

    Why abstract base class instead of just functions?
    1. Enforces interface: every extractor MUST implement extract() and _parse()
    2. Centralizes shared behavior: crawl logging, retry, DB connection
    3. Makes testing easier: mock _parse() to test extract() logic in isolation
    4. Enables the config-driven sources.yml pattern (Airflow reads this)

    Every concrete extractor must:
    - Set SOURCE_NAME class variable
    - Set SOURCE_TIER (1=API, 2=semi-structured, 3=scrape)
    - Implement extract(**kwargs) -> ExtractResult
    - Implement _parse(raw_data) -> dict
    """

    SOURCE_NAME: str = ""
    SOURCE_TIER: int = 1
    RAW_TABLE: str = ""      # e.g. "raw_nsf_awards"

    def __init__(self):
        self._conn = None
        self.logger = get_logger(f"extractor.{self.SOURCE_NAME}")

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_connection()
        return self._conn

    @abstractmethod
    def extract(self, **kwargs) -> ExtractResult:
        """
        Main entry point. Pull data from source, parse, validate, load to raw zone.
        Must write to self.RAW_TABLE. Must call self._log_crawl_event() at end.
        """
        ...

    @abstractmethod
    def _parse(self, raw_data: dict) -> dict:
        """
        Parse raw API response / HTML into a flat dict.
        Return dict with all fields we care about.
        Missing/null fields are fine — quality scorer handles them.
        """
        ...

    def _log_crawl_event(self, result: ExtractResult):
        """
        Write crawl result to raw_crawl_log.
        This powers the Pipeline Health dashboard (BQ-7, BQ-8).
        Never skip this — it's what makes the pipeline observable.
        """
        try:
            self.conn.execute(
                """
                INSERT INTO raw_crawl_log
                    (source_name, source_tier, status, records_found,
                     records_loaded, records_failed, quality_avg,
                     duration_secs, errors_json, crawled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result.source_name,
                    self.SOURCE_TIER,
                    result.status,
                    result.records_found,
                    result.records_loaded,
                    result.records_failed,
                    result.quality_avg,
                    result.duration_secs,
                    str(result.errors) if result.errors else "[]",
                    datetime.now(timezone.utc),
                ],
            )
            self.logger.info(
                "crawl_logged",
                source=result.source_name,
                status=result.status,
                loaded=result.records_loaded,
                duration=round(result.duration_secs, 1),
            )
        except Exception as e:
            # Never let logging failure kill the extractor
            self.logger.error("crawl_log_failed", error=str(e))
```

---

## Step 1.5 — Warehouse Initialization

Create `warehouse/schema/001_raw_tables.sql`:

```sql
-- warehouse/schema/001_raw_tables.sql
-- Raw zone: these tables store data EXACTLY as received from sources.
-- NEVER modify a raw record. NEVER delete. Append-only.
-- If source data changes, that is a new raw record.

-- NSF Award Search API
CREATE TABLE IF NOT EXISTS raw_nsf_awards (
    award_id        VARCHAR PRIMARY KEY,
    raw_json        JSON NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_page        INTEGER,
    row_hash        VARCHAR,   -- md5 of raw_json, for change detection
    source_url      VARCHAR
);

-- NIH RePORTER API v2
CREATE TABLE IF NOT EXISTS raw_nih_projects (
    project_num     VARCHAR PRIMARY KEY,
    raw_json        JSON NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fiscal_year     INTEGER,
    row_hash        VARCHAR
);

-- NSERC bulk CSV
CREATE TABLE IF NOT EXISTS raw_nserc_awards (
    row_id          INTEGER,   -- no natural key in CSV, use row number
    raw_csv_row     JSON NOT NULL,   -- CSV row stored as key-value JSON
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     VARCHAR,   -- which CSV file this came from
    row_hash        VARCHAR,
    PRIMARY KEY (row_id, source_file)
);

-- CIHR Open Data
CREATE TABLE IF NOT EXISTS raw_cihr_awards (
    row_id          INTEGER,
    raw_csv_row     JSON NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file     VARCHAR,
    row_hash        VARCHAR,
    PRIMARY KEY (row_id, source_file)
);

-- Pipeline crawl events — powers Pipeline Health dashboard
CREATE TABLE IF NOT EXISTS raw_crawl_log (
    id              INTEGER PRIMARY KEY,   -- DuckDB auto-increments with SEQUENCE
    source_name     VARCHAR NOT NULL,
    source_tier     INTEGER NOT NULL,
    status          VARCHAR NOT NULL,      -- 'success', 'partial', 'failed'
    records_found   INTEGER,
    records_loaded  INTEGER,
    records_failed  INTEGER,
    quality_avg     DOUBLE,
    duration_secs   DOUBLE,
    errors_json     VARCHAR,
    crawled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-increment sequence for crawl_log
CREATE SEQUENCE IF NOT EXISTS crawl_log_seq START 1;
ALTER TABLE raw_crawl_log ALTER COLUMN id SET DEFAULT nextval('crawl_log_seq');
```

Create `warehouse/schema/002_seed_data.sql`:

```sql
-- warehouse/schema/002_seed_data.sql
-- Static reference data that doesn't change frequently.
-- CIP code taxonomy (partial — add more as needed)

CREATE TABLE IF NOT EXISTS seed_cip_taxonomy (
    cip_code        VARCHAR PRIMARY KEY,   -- e.g. '11.07'
    field_name      VARCHAR NOT NULL,      -- e.g. 'Computer Science'
    parent_cip      VARCHAR,              -- e.g. '11'
    broad_category  VARCHAR NOT NULL,     -- 'STEM', 'Humanities', 'Social Sciences', 'Health'
    stem_flag       BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT OR IGNORE INTO seed_cip_taxonomy VALUES
    ('11',    'Computer and Information Sciences',         NULL,  'STEM',            TRUE),
    ('11.07', 'Computer Science',                          '11',  'STEM',            TRUE),
    ('11.08', 'Computer Software and Media Applications',  '11',  'STEM',            TRUE),
    ('14',    'Engineering',                               NULL,  'STEM',            TRUE),
    ('14.09', 'Computer Engineering',                      '14',  'STEM',            TRUE),
    ('26',    'Biological and Biomedical Sciences',        NULL,  'STEM',            TRUE),
    ('26.01', 'Biology/Biological Sciences',               '26',  'STEM',            TRUE),
    ('27',    'Mathematics and Statistics',                NULL,  'STEM',            TRUE),
    ('40',    'Physical Sciences',                         NULL,  'STEM',            TRUE),
    ('40.02', 'Astronomy and Astrophysics',                '40',  'STEM',            TRUE),
    ('40.05', 'Chemistry',                                 '40',  'STEM',            TRUE),
    ('40.08', 'Physics',                                   '40',  'STEM',            TRUE),
    ('51',    'Health Professions',                        NULL,  'Health',           FALSE),
    ('51.14', 'Medical Sciences',                          '51',  'Health',           FALSE),
    ('42',    'Psychology',                                NULL,  'Social Sciences',  FALSE),
    ('45',    'Social Sciences',                           NULL,  'Social Sciences',  FALSE),
    ('45.06', 'Economics',                                 '45',  'Social Sciences',  FALSE),
    ('45.11', 'Sociology',                                 '45',  'Social Sciences',  FALSE),
    ('23',    'English Language and Literature',           NULL,  'Humanities',       FALSE),
    ('38',    'Philosophy and Religious Studies',          NULL,  'Humanities',       FALSE),
    ('54',    'History',                                   NULL,  'Humanities',       FALSE),
    ('16',    'Foreign Languages and Linguistics',         NULL,  'Humanities',       FALSE);

-- Funding agency reference
CREATE TABLE IF NOT EXISTS seed_funding_agency (
    agency_code     VARCHAR PRIMARY KEY,
    agency_name     VARCHAR NOT NULL,
    country         VARCHAR NOT NULL,
    agency_type     VARCHAR,              -- 'federal', 'provincial', 'private'
    website         VARCHAR
);

INSERT OR IGNORE INTO seed_funding_agency VALUES
    ('NSF',   'National Science Foundation',                          'US',  'federal',    'https://nsf.gov'),
    ('NIH',   'National Institutes of Health',                        'US',  'federal',    'https://nih.gov'),
    ('DOE',   'Department of Energy',                                 'US',  'federal',    'https://doe.gov'),
    ('NASA',  'National Aeronautics and Space Administration',        'US',  'federal',    'https://nasa.gov'),
    ('NSERC', 'Natural Sciences and Engineering Research Council',    'CA',  'federal',    'https://nserc-crsng.gc.ca'),
    ('CIHR',  'Canadian Institutes of Health Research',              'CA',  'federal',    'https://cihr-irsc.gc.ca'),
    ('SSHRC', 'Social Sciences and Humanities Research Council',     'CA',  'federal',    'https://sshrc-crsh.gc.ca');
```

Create `warehouse/init_warehouse.py`:

```python
# warehouse/init_warehouse.py
"""
Run once to initialize the DuckDB warehouse.
Creates all raw tables and loads seed data.

Usage:
    python warehouse/init_warehouse.py
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.utils.db_connection import get_connection, close_connection
from extractors.utils.logger import get_logger

logger = get_logger("init_warehouse")


def init_warehouse():
    conn = get_connection()
    schema_dir = Path("warehouse/schema")

    # Execute schema files in order (001_, 002_, etc.)
    sql_files = sorted(schema_dir.glob("*.sql"))

    for sql_file in sql_files:
        logger.info("executing_schema_file", file=sql_file.name)
        sql = sql_file.read_text()

        # Split on semicolons to execute statement by statement
        # DuckDB doesn't always handle multiple statements in one execute()
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            try:
                conn.execute(stmt)
            except Exception as e:
                logger.warning("statement_warning", file=sql_file.name, error=str(e))

    logger.info("warehouse_initialized", tables=conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall())

    close_connection()


if __name__ == "__main__":
    init_warehouse()
    print("✓ Warehouse initialized successfully")
```

Run it:
```bash
python warehouse/init_warehouse.py
```

Expected output:
```
{"event": "warehouse_initialized", "tables": [...], "level": "info", ...}
✓ Warehouse initialized successfully
```

---

## Step 1.6 — Quality Scorer

Create `extractors/validators/quality_scorer.py`:

```python
# extractors/validators/quality_scorer.py
from typing import Any


# Fields required for a record to be "complete"
# Add/remove based on what your downstream models need
REQUIRED_FIELDS = {
    "nsf_award": ["award_id", "amount_usd", "institution_name", "pi_name",
                  "start_date", "end_date", "program_name"],
    "nih_project": ["project_num", "total_cost", "organization_name", "pi_name",
                    "project_start_date", "project_end_date", "activity_code"],
    "nserc_award": ["applicant_name", "institution", "amount", "fiscal_year",
                    "program_code"],
}


def compute_quality_score(record: dict, record_type: str) -> float:
    """
    Returns a completeness score between 0.0 and 1.0.

    Why track this?
    - Identifies which sources have poor data quality
    - Powers the Pipeline Health dashboard
    - Downstream mart models can filter low-quality records
    - Over time, you can see if source quality is improving or degrading

    Example:
        score = compute_quality_score({"award_id": "123", "amount_usd": None}, "nsf_award")
        # Returns 0.14 (1 of 7 required fields present)
    """
    required = REQUIRED_FIELDS.get(record_type, [])
    if not required:
        return 1.0  # Unknown type — don't penalize

    present = sum(
        1 for field in required
        if record.get(field) is not None and record.get(field) != ""
    )
    return round(present / len(required), 3)


def get_missing_fields(record: dict, record_type: str) -> list[str]:
    """Returns list of required fields that are missing or null."""
    required = REQUIRED_FIELDS.get(record_type, [])
    return [
        field for field in required
        if record.get(field) is None or record.get(field) == ""
    ]
```

---

## Step 1.7 — NSF Extractor

Create `extractors/federal_apis/nsf_extractor.py`:

```python
# extractors/federal_apis/nsf_extractor.py
"""
NSF Award Search API Extractor.

API docs: https://www.research.gov/common/webapi/awardapisearch-v1.htm
No authentication required. Rate limit: ~10 requests/minute is safe.

Key insight: NSF also provides bulk JSON files at:
https://www.nsf.gov/awardsearch/download.jsp
One JSON file per year (1960-present). For initial historical load,
download these directly instead of paginating the API.

For ongoing incremental updates, use the API with dateStart filter.
"""
import json
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from extractors.base import BaseExtractor, ExtractResult
from extractors.validators.quality_scorer import compute_quality_score
from extractors.utils.rate_limiter import RateLimiter
from config.settings import settings


class NSFExtractor(BaseExtractor):
    """
    Extracts NSF Award data into raw_nsf_awards table.

    Two modes:
    1. Incremental (default): pulls awards with dateStart >= N days ago
    2. Historical: pulls all awards for specific keywords/date ranges

    The distinction matters because the NSF API paginates by offset,
    and historical loads can return 50,000+ records. Always run historical
    loads with a specific keyword to bound the result set.
    """

    SOURCE_NAME = "nsf_api"
    SOURCE_TIER = 1
    RAW_TABLE = "raw_nsf_awards"
    BASE_URL = "https://api.nsf.gov/services/v1/awards.json"

    # Fields to request from NSF API
    # Full list: https://www.research.gov/common/webapi/awardapisearch-v1.htm
    PRINT_FIELDS = ",".join([
        "id", "title", "agency", "date", "startDate", "expDate",
        "fundsObligatedAmt", "piFirstName", "piLastName", "piEmail",
        "awardeeOrganization", "primaryProgram", "coPDPI",
        "abstractText", "keyword", "transType", "awardeeName"
    ])

    def __init__(self):
        super().__init__()
        self.rate_limiter = RateLimiter(
            requests_per_minute=settings.nsf_api_rate_limit
        )
        self.raw_dir = settings.raw_data_dir / "nsf"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def extract(
        self,
        keyword: str = "graduate fellowship doctoral",
        date_start: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> ExtractResult:
        """
        Pull NSF awards and store in raw zone.

        Args:
            keyword: Search keyword (NSF API supports AND/OR with +/|)
            date_start: Start date in MM/DD/YYYY format. Defaults to 30 days ago.
            max_pages: Limit pages for testing. None = pull everything.

        Returns:
            ExtractResult with counts and quality metrics
        """
        start_time = time.monotonic()

        if date_start is None:
            # Default: last 30 days for incremental updates
            thirty_days_ago = datetime.now() - timedelta(days=30)
            date_start = thirty_days_ago.strftime("%m/%d/%Y")

        self.logger.info(
            "nsf_extract_start",
            keyword=keyword,
            date_start=date_start,
        )

        records_found = 0
        records_loaded = 0
        records_failed = 0
        quality_scores = []
        errors = []
        page = 1

        while True:
            if max_pages and page > max_pages:
                self.logger.info("nsf_max_pages_reached", pages=page - 1)
                break

            try:
                awards = self._fetch_page(keyword, date_start, page)
            except Exception as e:
                error_msg = f"Page {page} fetch failed: {str(e)}"
                errors.append(error_msg)
                self.logger.error("nsf_page_fetch_failed", page=page, error=str(e))
                break

            if not awards:
                self.logger.info("nsf_extract_complete", pages=page - 1)
                break

            records_found += len(awards)

            # Archive raw page to file (raw zone)
            self._archive_raw_page(awards, page)

            # Load to DuckDB raw table
            for award in awards:
                try:
                    parsed = self._parse(award)
                    quality = compute_quality_score(parsed, "nsf_award")
                    quality_scores.append(quality)
                    self._upsert_raw(award, parsed.get("award_id", ""))
                    records_loaded += 1
                except Exception as e:
                    records_failed += 1
                    errors.append(f"Record parse failed: {str(e)}")

            self.logger.info(
                "nsf_page_processed",
                page=page,
                awards=len(awards),
                total=records_found,
            )
            page += 1

        quality_avg = (
            sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        )
        duration = time.monotonic() - start_time

        result = ExtractResult(
            source_name=self.SOURCE_NAME,
            records_found=records_found,
            records_loaded=records_loaded,
            records_failed=records_failed,
            quality_avg=round(quality_avg, 3),
            duration_secs=round(duration, 2),
            errors=errors[:10],  # Cap error list to avoid huge logs
        )

        self._log_crawl_event(result)
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_page(self, keyword: str, date_start: str, page: int) -> list[dict]:
        """
        Fetch one page of NSF results.
        The @retry decorator handles transient network failures automatically.
        After 3 failures it raises, which is caught by extract().
        """
        self.rate_limiter.wait()

        params = {
            "keyword": keyword,
            "dateStart": date_start,
            "offset": (page - 1) * 25,
            "rpp": 25,  # results per page (max 25 for NSF API)
            "printFields": self.PRINT_FIELDS,
        }

        response = requests.get(
            self.BASE_URL,
            params=params,
            timeout=30,
            headers={"User-Agent": "ScholarHub-DE/1.0 (research aggregation pipeline)"},
        )
        response.raise_for_status()

        data = response.json()
        awards = data.get("response", {}).get("award", [])

        # NSF returns empty list on last page, not an error
        return awards if awards else []

    def _parse(self, raw_award: dict) -> dict:
        """
        Parse raw NSF award dict into canonical schema.

        NSF API quirks:
        - fundsObligatedAmt is a STRING like "1234567", not a number
        - awardeeOrganization is a nested dict
        - Dates are "MM/DD/YYYY" strings
        - Missing fields return "" not None
        """
        org = raw_award.get("awardeeOrganization", {})
        if isinstance(org, str):
            # Sometimes NSF returns org as plain string
            org = {"name": org}

        # Parse amount: strip commas, convert to float
        amount_str = raw_award.get("fundsObligatedAmt", "") or ""
        try:
            amount_usd = float(amount_str.replace(",", ""))
        except (ValueError, AttributeError):
            amount_usd = None

        # Parse dates
        def parse_nsf_date(date_str: str) -> Optional[str]:
            if not date_str:
                return None
            try:
                return datetime.strptime(date_str, "%m/%d/%Y").date().isoformat()
            except ValueError:
                return None

        return {
            "award_id":       raw_award.get("id") or raw_award.get("awardeeName"),
            "title":          raw_award.get("title", "").strip() or None,
            "amount_usd":     amount_usd,
            "institution_name": org.get("name") or org.get("awardeeName"),
            "institution_city": org.get("city"),
            "institution_state": org.get("stateName") or org.get("stateCode"),
            "pi_name":        " ".join(filter(None, [
                                  raw_award.get("piFirstName", ""),
                                  raw_award.get("piLastName", ""),
                              ])).strip() or None,
            "pi_email":       raw_award.get("piEmail") or None,
            "program_name":   raw_award.get("primaryProgram") or None,
            "agency":         raw_award.get("agency", "NSF"),
            "start_date":     parse_nsf_date(raw_award.get("startDate", "")),
            "end_date":       parse_nsf_date(raw_award.get("expDate", "")),
            "abstract":       raw_award.get("abstractText") or None,
            "keywords":       raw_award.get("keyword") or None,
            "trans_type":     raw_award.get("transType") or None,  # 'Grant', 'Contract', etc.
        }

    def _upsert_raw(self, raw_award: dict, award_id: str):
        """
        Insert raw award into DuckDB. Skip if award_id already exists.
        Why INSERT OR IGNORE? Raw zone is append-only. If we've seen this
        award before, the raw record is unchanged — don't overwrite it.
        """
        raw_json = json.dumps(raw_award)
        row_hash = hashlib.md5(raw_json.encode()).hexdigest()

        self.conn.execute(
            """
            INSERT INTO raw_nsf_awards (award_id, raw_json, extracted_at, row_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (award_id) DO NOTHING
            """,
            [award_id, raw_json, datetime.now(timezone.utc), row_hash],
        )

    def _archive_raw_page(self, awards: list[dict], page: int):
        """
        Save raw API response to file system as backup.
        This is the permanent archive — DuckDB is for querying.
        If DuckDB is ever corrupted, you can rebuild from these files.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.raw_dir / f"nsf_page_{page:04d}_{timestamp}.json"
        filename.write_text(json.dumps(awards, indent=2))
```

---

## Step 1.8 — Test the Extractor

Create `tests/unit/test_nsf_extractor.py`:

```python
# tests/unit/test_nsf_extractor.py
"""
Unit tests for NSF extractor.
Tests use fixture data — no real API calls.
"""
import pytest
from unittest.mock import patch, MagicMock
from extractors.federal_apis.nsf_extractor import NSFExtractor


# Realistic NSF API response fixture
NSF_AWARD_FIXTURE = {
    "id": "2401234",
    "title": "Graduate Research Fellowship for Computer Science",
    "agency": "NSF",
    "date": "01/15/2024",
    "startDate": "09/01/2024",
    "expDate": "08/31/2027",
    "fundsObligatedAmt": "450000",
    "piFirstName": "Jane",
    "piLastName": "Smith",
    "piEmail": "jsmith@university.edu",
    "awardeeOrganization": {
        "name": "University of British Columbia",
        "city": "Vancouver",
        "stateName": "British Columbia",
    },
    "primaryProgram": "CISE/Division of Computing",
    "abstractText": "This grant supports doctoral training in machine learning...",
    "keyword": "machine learning, neural networks, PhD training",
    "transType": "Grant",
}

NSF_AWARD_MISSING_FIELDS = {
    "id": "2401235",
    "title": "Another Grant",
    # Missing: fundsObligatedAmt, piFirstName, piLastName, startDate, etc.
}


class TestNSFExtractorParse:
    """Test _parse() without touching the database or API."""

    def setup_method(self):
        self.extractor = NSFExtractor()

    def test_parse_complete_record(self):
        result = self.extractor._parse(NSF_AWARD_FIXTURE)

        assert result["award_id"] == "2401234"
        assert result["amount_usd"] == 450000.0
        assert result["pi_name"] == "Jane Smith"
        assert result["institution_name"] == "University of British Columbia"
        assert result["start_date"] == "2024-09-01"
        assert result["end_date"] == "2027-08-31"

    def test_parse_amount_with_commas(self):
        award = {**NSF_AWARD_FIXTURE, "fundsObligatedAmt": "1,234,567"}
        result = self.extractor._parse(award)
        assert result["amount_usd"] == 1234567.0

    def test_parse_missing_amount(self):
        award = {**NSF_AWARD_FIXTURE, "fundsObligatedAmt": ""}
        result = self.extractor._parse(award)
        assert result["amount_usd"] is None

    def test_parse_missing_fields_returns_none(self):
        result = self.extractor._parse(NSF_AWARD_MISSING_FIELDS)
        assert result["amount_usd"] is None
        assert result["pi_name"] is None or result["pi_name"] == ""


class TestQualityScorer:
    """Test quality scoring logic."""

    def test_complete_record_scores_high(self):
        from extractors.validators.quality_scorer import compute_quality_score
        extractor = NSFExtractor()
        parsed = extractor._parse(NSF_AWARD_FIXTURE)
        score = compute_quality_score(parsed, "nsf_award")
        assert score >= 0.8

    def test_sparse_record_scores_low(self):
        from extractors.validators.quality_scorer import compute_quality_score
        extractor = NSFExtractor()
        parsed = extractor._parse(NSF_AWARD_MISSING_FIELDS)
        score = compute_quality_score(parsed, "nsf_award")
        assert score < 0.5
```

Run tests:
```bash
pytest tests/unit/test_nsf_extractor.py -v
```

---

## Step 1.9 — Run the Full Pipeline

Now run the extractor for real:

```bash
# First, initialize the warehouse
python warehouse/init_warehouse.py

# Run NSF extractor (pulls last 30 days, ~50-200 awards)
python -c "
from extractors.federal_apis.nsf_extractor import NSFExtractor
extractor = NSFExtractor()
result = extractor.extract(
    keyword='graduate fellowship doctoral',
    max_pages=3   # Start small: 3 pages = 75 awards
)
print(f'Status: {result.status}')
print(f'Found: {result.records_found}')
print(f'Loaded: {result.records_loaded}')
print(f'Quality avg: {result.quality_avg}')
"
```

Verify data in DuckDB:
```bash
python -c "
import duckdb
conn = duckdb.connect('warehouse/scholarhub.duckdb')

# Check raw records
count = conn.execute('SELECT COUNT(*) FROM raw_nsf_awards').fetchone()[0]
print(f'Raw NSF records: {count}')

# Check crawl log
log = conn.execute('''
    SELECT source_name, status, records_loaded, quality_avg, duration_secs, crawled_at
    FROM raw_crawl_log
    ORDER BY crawled_at DESC
    LIMIT 5
''').fetchdf()
print(log.to_string())

# Preview a record
sample = conn.execute('''
    SELECT award_id,
           raw_json->>'title' AS title,
           raw_json->>'fundsObligatedAmt' AS amount,
           raw_json->'awardeeOrganization'->>'name' AS institution
    FROM raw_nsf_awards
    LIMIT 3
''').fetchdf()
print(sample.to_string())
"
```

---

## Phase 1 Checklist

Before moving to Phase 2, verify:

- [ ] `python warehouse/init_warehouse.py` runs without errors
- [ ] `pytest tests/unit/test_nsf_extractor.py` — all tests pass
- [ ] NSF extractor runs and loads 50+ records into `raw_nsf_awards`
- [ ] `raw_crawl_log` has an entry showing `status = 'success'`
- [ ] DuckDB file exists at `warehouse/scholarhub.duckdb`
- [ ] Raw JSON files archived under `data/raw/nsf/`

**What you've built:**
- A raw zone that is immutable and append-only
- An observable pipeline (every run logged to `raw_crawl_log`)
- Quality scoring on every record ingested
- Retry logic for transient API failures
- Unit tests that don't require API calls

**Next:** Phase 2 — dbt transforms to turn raw data into queryable mart tables.
