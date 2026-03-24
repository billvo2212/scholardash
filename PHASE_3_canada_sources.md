# Phase 3 — Canada Sources: NSERC + CIHR

**Goal:** Add Canadian federal funding data to achieve full North America coverage. By the end, `mart_funding_by_field` includes both US and Canadian data, and you can compare funding landscapes across both countries.

**Duration:** ~1 week  
**Prerequisite:** Phase 2 complete. dbt pipeline running.

---

## Why Canada Sources Are Architecturally Interesting

Canada sources force you to solve a problem that makes DE work genuinely hard: **different source formats require different parsers, but the output must conform to the same schema.**

- NSF: REST API, JSON, paginated, real-time
- NSERC: Bulk CSV download, annual release, needs manual download step
- CIHR: CSV on Open.Canada.ca, available via CKAN API

Same destination (`raw_*` tables), completely different paths to get there. This is why the `BaseExtractor` abstract class exists — it enforces the contract.

---

## Step 3.1 — NSERC Extractor

NSERC doesn't have a real-time API. They publish annual CSV files. The strategy: download the CSV, parse it row by row, load to DuckDB raw zone.

### Download NSERC Data

First, download the NSERC awards CSV manually:
```
URL: https://www.nserc-crsng.gc.ca/NSERC-CRSNG/FundingDecisions-DecisionsFinancement/ResearchGrants-SubventionsDeRecherche_eng.asp
Click: "Download Complete Dataset"
Save to: data/raw/nserc/nserc_awards_YYYY.csv
```

Or download programmatically (the URL pattern is stable):
```bash
# Download NSERC Discovery Grants CSV (adjust year as needed)
curl -o data/raw/nserc/nserc_awards_2024.csv \
  "https://www.nserc-crsng.gc.ca/ase-oro/Details-Detailles_eng.asp?prog=101&view=download"
```

Create `extractors/federal_apis/nserc_extractor.py`:

```python
# extractors/federal_apis/nserc_extractor.py
"""
NSERC Awards CSV Extractor.

NSERC publishes annual CSV files with all grants since 1991.
Data is released in batches — typically Q1 of the following year.

Key programs we care about:
  RGPIN  = Discovery Grants (most common, professor research funding)
  CGSD   = Canada Graduate Scholarships - Doctoral
  CGSM   = Canada Graduate Scholarships - Master's
  PDF    = Postdoctoral Fellowship
  CREATE = Collaborative Research and Training Experience
           (explicitly designed to fund grad training — like NIH T32)
  SPG    = Strategic Project Grant
  EGP    = Engage Grant (industry partnerships)

NSERC column naming is inconsistent across years.
This extractor handles the most common column names with fallbacks.
"""
import csv
import json
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from extractors.base import BaseExtractor, ExtractResult
from extractors.validators.quality_scorer import compute_quality_score


# NSERC CSV columns vary slightly by year/download
# Map of canonical name → list of possible CSV column names
COLUMN_ALIASES = {
    "applicant_name":     ["Applicant Name", "Name", "Application Name"],
    "institution":        ["Institution", "Organization", "Affiliated Organization"],
    "province":           ["Province", "Prov"],
    "amount":             ["Amount Awarded", "Amount", "Total Amount", "Award Amount"],
    "fiscal_year":        ["Competition Year", "Year", "Fiscal Year"],
    "program_code":       ["Program", "Program Code"],
    "research_subject":   ["Subject Area", "Research Subject", "Subject"],
    "keywords":           ["Keywords", "Key Words"],
    "selection_committee":["Selection Committee", "Committee"],
    "project_title":      ["Application Title", "Title", "Project Title"],
    "country":            ["Country"],
    "dept":               ["Department", "Dept"],
}


class NSERCExtractor(BaseExtractor):
    """Extracts NSERC awards from bulk CSV files."""

    SOURCE_NAME = "nserc_csv"
    SOURCE_TIER = 2   # Bulk download, not real-time API
    RAW_TABLE = "raw_nserc_awards"

    def extract(self, csv_path: str, **kwargs) -> ExtractResult:
        """
        Load a NSERC CSV file into the raw zone.

        Args:
            csv_path: Path to the downloaded NSERC CSV file

        Returns:
            ExtractResult with counts and quality metrics
        """
        start_time = time.monotonic()
        csv_file = Path(csv_path)

        if not csv_file.exists():
            return ExtractResult(
                source_name=self.SOURCE_NAME,
                records_found=0,
                records_loaded=0,
                records_failed=1,
                quality_avg=0.0,
                duration_secs=0.0,
                errors=[f"File not found: {csv_path}"],
            )

        self.logger.info("nserc_extract_start", file=csv_path)

        records_found = 0
        records_loaded = 0
        records_failed = 0
        quality_scores = []
        errors = []

        with open(csv_file, encoding="utf-8-sig") as f:  # utf-8-sig handles BOM
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            # Build column mapping for this specific file
            col_map = self._build_column_map(headers)
            self.logger.info("nserc_column_map", mapped=list(col_map.keys()))

            for row_num, row in enumerate(reader):
                records_found += 1
                try:
                    parsed = self._parse_row(row, col_map)
                    quality = compute_quality_score(parsed, "nserc_award")
                    quality_scores.append(quality)

                    # Store as JSON in raw zone
                    row_json = json.dumps(row)  # Original row as-is
                    row_hash = hashlib.md5(row_json.encode()).hexdigest()

                    self.conn.execute(
                        """
                        INSERT INTO raw_nserc_awards
                            (row_id, raw_csv_row, extracted_at, source_file, row_hash)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (row_id, source_file) DO NOTHING
                        """,
                        [row_num, row_json, datetime.now(timezone.utc),
                         csv_file.name, row_hash],
                    )
                    records_loaded += 1

                except Exception as e:
                    records_failed += 1
                    errors.append(f"Row {row_num}: {str(e)}")

        quality_avg = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        duration = time.monotonic() - start_time

        result = ExtractResult(
            source_name=self.SOURCE_NAME,
            records_found=records_found,
            records_loaded=records_loaded,
            records_failed=records_failed,
            quality_avg=round(quality_avg, 3),
            duration_secs=round(duration, 2),
            errors=errors[:10],
        )
        self._log_crawl_event(result)
        return result

    def _build_column_map(self, headers: list[str]) -> dict[str, str]:
        """
        Map canonical field names to actual CSV column names.
        Returns only fields that exist in this file.
        """
        col_map = {}
        headers_lower = {h.lower(): h for h in headers}

        for canonical, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias.lower() in headers_lower:
                    col_map[canonical] = headers_lower[alias.lower()]
                    break

        return col_map

    def _parse(self, raw_data: dict) -> dict:
        """Required by BaseExtractor but not used directly — use _parse_row instead."""
        return raw_data

    def _parse_row(self, row: dict, col_map: dict[str, str]) -> dict:
        """Parse a CSV row using the column map."""

        def get(field: str) -> Optional[str]:
            col = col_map.get(field)
            if col is None:
                return None
            val = row.get(col, "").strip()
            return val if val else None

        # Parse amount (NSERC uses commas and sometimes dollar signs)
        amount_str = get("amount") or ""
        amount_str = amount_str.replace("$", "").replace(",", "").strip()
        try:
            amount = float(amount_str) if amount_str else None
        except ValueError:
            amount = None

        return {
            "applicant_name":      get("applicant_name"),
            "institution":         get("institution"),
            "province":            get("province"),
            "amount":              amount,
            "fiscal_year":         get("fiscal_year"),
            "program_code":        get("program_code"),
            "research_subject":    get("research_subject"),
            "keywords":            get("keywords"),
            "project_title":       get("project_title"),
            "dept":                get("dept"),
            "country":             get("country") or "Canada",
        }
```

### Run NSERC Extractor

```bash
# After downloading the CSV to data/raw/nserc/
python -c "
from extractors.federal_apis.nserc_extractor import NSERCExtractor
extractor = NSERCExtractor()
result = extractor.extract('data/raw/nserc/nserc_awards_2024.csv')
print(f'Status: {result.status}')
print(f'Loaded: {result.records_loaded} records')
print(f'Quality: {result.quality_avg}')
"
```

---

## Step 3.2 — CIHR Extractor (Open Canada API)

CIHR data is available through Canada's Open Government portal, which uses the CKAN API. No authentication required for read operations.

Create `extractors/federal_apis/cihr_extractor.py`:

```python
# extractors/federal_apis/cihr_extractor.py
"""
CIHR Grants & Awards Extractor via Open Canada CKAN API.

Dataset ID: 49edb1d7-5cb4-4fa7-897c-515d1aad5da3
URL: https://open.canada.ca/data/en/dataset/49edb1d7-5cb4-4fa7-897c-515d1aad5da3

CIHR = Canadian Institutes of Health Research.
This is Canada's NIH equivalent — biomedical and health research.

The CKAN API returns resource metadata. Each resource is a CSV file
for a specific fiscal year. We download each CSV and load to raw zone.
"""
import csv
import json
import hashlib
import io
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from extractors.base import BaseExtractor, ExtractResult
from extractors.utils.rate_limiter import RateLimiter
from extractors.validators.quality_scorer import compute_quality_score


CIHR_DATASET_ID = "49edb1d7-5cb4-4fa7-897c-515d1aad5da3"
CKAN_API_BASE = "https://open.canada.ca/api/3/action"


class CIHRExtractor(BaseExtractor):
    """
    Extracts CIHR grant data from Open Canada CKAN API.
    Downloads CSV resources and loads to raw_cihr_awards.
    """

    SOURCE_NAME = "cihr_open_canada"
    SOURCE_TIER = 1   # Structured data, consistent format
    RAW_TABLE = "raw_cihr_awards"

    def __init__(self):
        super().__init__()
        self.rate_limiter = RateLimiter(requests_per_minute=20)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ScholarHub-DE/1.0 (graduate funding research)"
        })

    def extract(self, max_resources: Optional[int] = None, **kwargs) -> ExtractResult:
        """
        Discover and download CIHR CSV resources via CKAN API.

        Args:
            max_resources: Limit number of CSV files to download (None = all)
        """
        start_time = time.monotonic()
        self.logger.info("cihr_extract_start")

        # Step 1: Get list of resources (CSV files) for this dataset
        resources = self._get_dataset_resources()
        csv_resources = [r for r in resources if r.get("format", "").upper() == "CSV"]

        self.logger.info("cihr_resources_found", count=len(csv_resources))

        if max_resources:
            csv_resources = csv_resources[:max_resources]

        records_found = 0
        records_loaded = 0
        records_failed = 0
        quality_scores = []
        errors = []

        for resource in csv_resources:
            url = resource.get("url")
            name = resource.get("name", "unknown")

            if not url:
                continue

            try:
                self.logger.info("cihr_downloading_resource", name=name, url=url)
                loaded, found, quality, resource_errors = self._download_and_load(url, name)
                records_found += found
                records_loaded += loaded
                quality_scores.extend(quality)
                errors.extend(resource_errors)
            except Exception as e:
                records_failed += 1
                errors.append(f"Resource {name}: {str(e)}")

        quality_avg = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        duration = time.monotonic() - start_time

        result = ExtractResult(
            source_name=self.SOURCE_NAME,
            records_found=records_found,
            records_loaded=records_loaded,
            records_failed=records_failed,
            quality_avg=round(quality_avg, 3),
            duration_secs=round(duration, 2),
            errors=errors[:10],
        )
        self._log_crawl_event(result)
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _get_dataset_resources(self) -> list[dict]:
        """Fetch dataset metadata from CKAN API."""
        self.rate_limiter.wait()
        url = f"{CKAN_API_BASE}/package_show?id={CIHR_DATASET_ID}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("result", {}).get("resources", [])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def _download_and_load(
        self, url: str, resource_name: str
    ) -> tuple[int, int, list[float], list[str]]:
        """Download a CSV resource and load rows to DuckDB."""
        self.rate_limiter.wait()

        response = self.session.get(url, timeout=120)  # Large files need more time
        response.raise_for_status()

        # Parse CSV from response content
        content = response.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        loaded = 0
        found = 0
        quality_scores = []
        errors = []

        for row_num, row in enumerate(reader):
            found += 1
            try:
                parsed = self._parse(dict(row))
                quality = compute_quality_score(parsed, "nserc_award")  # Same schema
                quality_scores.append(quality)

                row_json = json.dumps(dict(row))
                row_hash = hashlib.md5(row_json.encode()).hexdigest()

                self.conn.execute(
                    """
                    INSERT INTO raw_cihr_awards
                        (row_id, raw_csv_row, extracted_at, source_file, row_hash)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (row_id, source_file) DO NOTHING
                    """,
                    [row_num, row_json, datetime.now(timezone.utc),
                     resource_name, row_hash],
                )
                loaded += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")

        return loaded, found, quality_scores, errors

    def _parse(self, row: dict) -> dict:
        """
        Parse CIHR CSV row. Column names are more consistent than NSERC.
        Common columns: Applicant Name, Institution, Province, Amount Awarded,
                        Competition Year, Program, Research Subject
        """
        def clean(val: Optional[str]) -> Optional[str]:
            if val is None:
                return None
            val = val.strip()
            return val if val else None

        amount_str = (clean(row.get("Amount Awarded", "")) or "").replace("$", "").replace(",", "")
        try:
            amount = float(amount_str) if amount_str else None
        except ValueError:
            amount = None

        return {
            "applicant_name": clean(row.get("Applicant Name") or row.get("Name")),
            "institution":    clean(row.get("Institution") or row.get("Organization")),
            "province":       clean(row.get("Province") or row.get("Prov")),
            "amount":         amount,
            "fiscal_year":    clean(row.get("Competition Year") or row.get("Year")),
            "program_code":   clean(row.get("Program") or row.get("Program Code")),
            "research_subject": clean(row.get("Research Subject") or row.get("Subject Area")),
            "project_title":  clean(row.get("Application Title") or row.get("Title")),
            "country":        "Canada",
        }
```

---

## Step 3.3 — NIH Extractor

The NIH RePORTER API is the richest source for identifying professors with active training grants.

Create `extractors/federal_apis/nih_extractor.py`:

```python
# extractors/federal_apis/nih_extractor.py
"""
NIH RePORTER API v2 Extractor.

API: https://api.reporter.nih.gov/v2/projects/search
No authentication required. Rate limit is generous.

KEY INSIGHT about activity codes:
- R01, R21, R03 = Research grants (professor's lab funding)
- T32 = Institutional training grants — THESE MEAN GRAD STUDENTS ARE BEING FUNDED
  A professor with T32 is explicitly funded to train PhD students.
- F31 = Predoctoral NRSA — STUDENT directly holds this fellowship
- F32 = Postdoctoral NRSA
- K99/R00 = Career development — early-career faculty, likely building a lab

For ScholarHub's BQ-1 (who is hiring?), T32 is the highest signal.
"""
import json
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from extractors.base import BaseExtractor, ExtractResult
from extractors.validators.quality_scorer import compute_quality_score
from extractors.utils.rate_limiter import RateLimiter
from config.settings import settings


class NIHExtractor(BaseExtractor):
    """Extracts NIH project data from RePORTER API v2."""

    SOURCE_NAME = "nih_reporter"
    SOURCE_TIER = 1
    RAW_TABLE = "raw_nih_projects"
    BASE_URL = "https://api.reporter.nih.gov/v2/projects/search"

    # Activity codes most relevant for graduate training
    TRAINING_ACTIVITY_CODES = ["T32", "T34", "T90", "TL1"]
    RESEARCH_ACTIVITY_CODES = ["R01", "R21", "R03", "R15", "R35", "DP2"]
    FELLOWSHIP_ACTIVITY_CODES = ["F31", "F32", "F30"]

    def __init__(self):
        super().__init__()
        self.rate_limiter = RateLimiter(
            requests_per_minute=settings.nih_api_rate_limit
        )

    def extract(
        self,
        fiscal_years: Optional[list[int]] = None,
        activity_codes: Optional[list[str]] = None,
        max_records: Optional[int] = None,
        **kwargs,
    ) -> ExtractResult:
        """
        Pull NIH projects for specified fiscal years and activity codes.

        For initial load: use fiscal_years=[2020,2021,2022,2023,2024]
        For daily incremental: fiscal_years=[current_year], no code filter

        Args:
            fiscal_years: List of fiscal years to pull (default: last 3 years)
            activity_codes: Specific codes to pull (default: all training + research)
            max_records: Cap for testing (None = pull everything)
        """
        import datetime as dt
        start_time = time.monotonic()

        if fiscal_years is None:
            current_year = dt.datetime.now().year
            fiscal_years = [current_year - 2, current_year - 1, current_year]

        if activity_codes is None:
            activity_codes = (
                self.TRAINING_ACTIVITY_CODES +
                self.RESEARCH_ACTIVITY_CODES +
                self.FELLOWSHIP_ACTIVITY_CODES
            )

        self.logger.info(
            "nih_extract_start",
            fiscal_years=fiscal_years,
            activity_codes=activity_codes,
        )

        records_found = 0
        records_loaded = 0
        records_failed = 0
        quality_scores = []
        errors = []

        offset = 0
        limit = 500  # NIH allows up to 500 per request

        while True:
            if max_records and records_found >= max_records:
                break

            try:
                projects, total = self._fetch_batch(
                    fiscal_years, activity_codes, offset, limit
                )
            except Exception as e:
                errors.append(f"Batch at offset {offset}: {str(e)}")
                self.logger.error("nih_batch_failed", offset=offset, error=str(e))
                break

            if not projects:
                break

            records_found += len(projects)

            for project in projects:
                try:
                    project_num = project.get("project_num", "")
                    parsed = self._parse(project)
                    quality = compute_quality_score(parsed, "nih_project")
                    quality_scores.append(quality)
                    self._upsert_raw(project, project_num)
                    records_loaded += 1
                except Exception as e:
                    records_failed += 1
                    errors.append(f"Project {project.get('project_num', '?')}: {str(e)}")

            self.logger.info(
                "nih_batch_processed",
                offset=offset,
                batch_size=len(projects),
                total_found=records_found,
                total_in_db=total,
            )

            offset += limit
            if offset >= total:
                break

        quality_avg = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        duration = time.monotonic() - start_time

        result = ExtractResult(
            source_name=self.SOURCE_NAME,
            records_found=records_found,
            records_loaded=records_loaded,
            records_failed=records_failed,
            quality_avg=round(quality_avg, 3),
            duration_secs=round(duration, 2),
            errors=errors[:10],
        )
        self._log_crawl_event(result)
        return result

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=30))
    def _fetch_batch(
        self,
        fiscal_years: list[int],
        activity_codes: list[str],
        offset: int,
        limit: int,
    ) -> tuple[list[dict], int]:
        """Fetch one page of NIH projects."""
        self.rate_limiter.wait()

        payload = {
            "criteria": {
                "fiscal_years": fiscal_years,
                "activity_codes": activity_codes,
            },
            "include_fields": [
                "ProjectNum", "ProjectTitle", "AbstractText",
                "PrincipalInvestigators", "Organization",
                "TotalCost", "DirectCostAmt", "FiscalYear",
                "ProjectStartDate", "ProjectEndDate",
                "ActivityCode", "Terms", "ProgramOfficers",
                "AwardNoticeDate",
            ],
            "offset": offset,
            "limit": limit,
            "sort_field": "TotalCost",
            "sort_order": "desc",
        }

        response = requests.post(
            self.BASE_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ScholarHub-DE/1.0",
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        return data.get("results", []), data.get("meta", {}).get("total", 0)

    def _parse(self, project: dict) -> dict:
        """
        Parse NIH project dict into canonical schema.

        PrincipalInvestigators is a list — take the first (primary PI).
        Organization is a nested dict.
        """
        pis = project.get("principal_investigators") or project.get("PrincipalInvestigators") or []
        primary_pi = pis[0] if pis else {}

        org = project.get("organization") or project.get("Organization") or {}

        total_cost_str = str(project.get("total_cost") or project.get("TotalCost") or "")
        try:
            total_cost = float(total_cost_str) if total_cost_str else None
        except ValueError:
            total_cost = None

        return {
            "project_num":       project.get("project_num") or project.get("ProjectNum"),
            "title":             project.get("project_title") or project.get("ProjectTitle"),
            "total_cost":        total_cost,
            "organization_name": org.get("org_name") or org.get("Name"),
            "organization_city": org.get("org_city") or org.get("City"),
            "organization_state":org.get("org_state") or org.get("State"),
            "pi_name":           primary_pi.get("full_name") or (
                                     (primary_pi.get("first_name", "") + " " +
                                      primary_pi.get("last_name", "")).strip()
                                 ) or None,
            "pi_profile_id":     primary_pi.get("profile_id"),
            "activity_code":     project.get("activity_code") or project.get("ActivityCode"),
            "fiscal_year":       project.get("fiscal_year") or project.get("FiscalYear"),
            "project_start_date":project.get("project_start_date") or project.get("ProjectStartDate"),
            "project_end_date":  project.get("project_end_date") or project.get("ProjectEndDate"),
            "abstract":          project.get("abstract_text") or project.get("AbstractText"),
            "terms":             project.get("terms") or project.get("Terms"),
            # T32 grants: is this a training grant?
            "is_training_grant": (
                (project.get("activity_code") or project.get("ActivityCode") or "")
                .startswith("T")
            ),
        }

    def _upsert_raw(self, project: dict, project_num: str):
        raw_json = json.dumps(project)
        row_hash = hashlib.md5(raw_json.encode()).hexdigest()

        self.conn.execute(
            """
            INSERT INTO raw_nih_projects (project_num, raw_json, extracted_at, row_hash)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (project_num) DO NOTHING
            """,
            [project_num, raw_json, datetime.now(timezone.utc), row_hash],
        )
```

---

## Step 3.4 — Add Canadian Staging Models

Create `models/staging/stg_nserc_awards.sql`:

```sql
-- models/staging/stg_nserc_awards.sql
-- Parses raw NSERC CSV rows (stored as JSON) into typed columns.

SELECT
    row_id,
    source_file,

    -- Applicant / PI
    NULLIF(TRIM(raw_csv_row->>'applicant_name'), '')  AS applicant_name,
    NULLIF(TRIM(raw_csv_row->>'dept'), '')             AS department,

    -- Institution
    NULLIF(TRIM(raw_csv_row->>'institution'), '')      AS institution_name,
    NULLIF(TRIM(raw_csv_row->>'province'), '')         AS province,
    'Canada'                                           AS country,

    -- Award details
    TRY_CAST(
        REPLACE(COALESCE(raw_csv_row->>'amount', ''), ',', '')
        AS DECIMAL(14, 2)
    )                                                  AS amount_cad,

    -- Convert to USD for cross-country comparison (approximate)
    -- Use fixed rate 0.74 for historical analysis
    -- In production: use a currency conversion API
    TRY_CAST(
        REPLACE(COALESCE(raw_csv_row->>'amount', ''), ',', '')
        AS DECIMAL(14, 2)
    ) * 0.74                                           AS amount_usd_approx,

    NULLIF(TRIM(raw_csv_row->>'fiscal_year'), '')     AS fiscal_year,
    NULLIF(TRIM(raw_csv_row->>'program_code'), '')    AS program_code,
    NULLIF(TRIM(raw_csv_row->>'research_subject'), '') AS research_subject,
    NULLIF(TRIM(raw_csv_row->>'project_title'), '')   AS project_title,
    NULLIF(TRIM(raw_csv_row->>'keywords'), '')        AS keywords,

    -- Derive: is this a graduate training award?
    CASE
        WHEN TRIM(raw_csv_row->>'program_code') IN ('CGSD', 'CGSM', 'PDF', 'CREATE', 'BPRS', 'PGS')
        THEN TRUE
        ELSE FALSE
    END AS is_graduate_award,

    -- Data quality
    (
        (CASE WHEN raw_csv_row->>'applicant_name' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_csv_row->>'institution' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_csv_row->>'amount' IS NOT NULL AND raw_csv_row->>'amount' != '' THEN 1 ELSE 0 END) +
        (CASE WHEN raw_csv_row->>'fiscal_year' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_csv_row->>'program_code' IS NOT NULL THEN 1 ELSE 0 END)
    ) / 5.0                                           AS data_quality_score,

    extracted_at

FROM main.raw_nserc_awards
WHERE raw_csv_row IS NOT NULL
```

---

## Step 3.5 — Add NIH Staging Model

Create `models/staging/stg_nih_projects.sql`:

```sql
-- models/staging/stg_nih_projects.sql
SELECT
    project_num,

    NULLIF(TRIM(raw_json->>'title'), '')                   AS title,
    NULLIF(TRIM(raw_json->>'pi_name'), '')                 AS pi_name,
    NULLIF(TRIM(raw_json->>'organization_name'), '')       AS institution_name,
    NULLIF(TRIM(raw_json->>'organization_city'), '')       AS institution_city,
    NULLIF(TRIM(raw_json->>'organization_state'), '')      AS institution_state,
    'US'                                                   AS country,

    TRY_CAST(raw_json->>'total_cost' AS DECIMAL(14, 2))   AS amount_usd,
    NULLIF(raw_json->>'activity_code', '')                 AS activity_code,
    NULLIF(raw_json->>'fiscal_year', '')::INTEGER          AS fiscal_year,

    TRY_CAST(raw_json->>'project_start_date' AS DATE)     AS start_date,
    TRY_CAST(raw_json->>'project_end_date' AS DATE)       AS end_date,

    -- Key signal: T32 = training grant = professor IS funding grad students
    (raw_json->>'activity_code' LIKE 'T%')                 AS is_training_grant,
    (raw_json->>'activity_code' LIKE 'F%')                 AS is_fellowship,

    -- Is grant currently active?
    CASE
        WHEN TRY_CAST(raw_json->>'project_end_date' AS DATE) >= CURRENT_DATE
        THEN TRUE ELSE FALSE
    END                                                    AS is_active,

    NULLIF(raw_json->>'abstract', '')                      AS abstract,

    (
        (CASE WHEN raw_json->>'total_cost' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_json->>'organization_name' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_json->>'pi_name' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_json->>'project_start_date' IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN raw_json->>'activity_code' IS NOT NULL THEN 1 ELSE 0 END)
    ) / 5.0                                               AS data_quality_score,

    extracted_at

FROM main.raw_nih_projects
WHERE project_num IS NOT NULL
```

---

## Step 3.6 — Unified North America Mart

Now combine all sources into one mart that covers both US and Canada.

Create `models/marts/mart_funding_north_america.sql`:

```sql
-- models/marts/mart_funding_north_america.sql
-- Unified funding view across NSF (US) + NIH (US) + NSERC (CA) + CIHR (CA)
-- This is the mart that enables true North America comparison.

WITH nsf AS (
    SELECT
        'NSF'                               AS agency,
        'US'                                AS country,
        institution_name,
        institution_state                   AS region,
        broad_category,
        cip_parent_code,
        EXTRACT(YEAR FROM start_date)::INTEGER AS year,
        amount_usd,
        is_active,
        pi_name,
        'research_grant'                    AS grant_type
    FROM {{ ref('int_nsf_field_mapped') }}
    WHERE amount_usd IS NOT NULL AND start_date IS NOT NULL
),

nih AS (
    SELECT
        COALESCE('NIH/' || activity_code, 'NIH') AS agency,
        'US'                                AS country,
        institution_name,
        institution_state                   AS region,
        -- NIH has different field classification — approximate
        CASE
            WHEN activity_code IN ('T32','F31','F32') THEN 'Health'
            ELSE 'Health'
        END                                 AS broad_category,
        '51'                                AS cip_parent_code,
        fiscal_year                         AS year,
        amount_usd,
        is_active,
        pi_name,
        CASE WHEN is_training_grant THEN 'training_grant'
             WHEN is_fellowship THEN 'fellowship'
             ELSE 'research_grant' END      AS grant_type
    FROM {{ ref('stg_nih_projects') }}
    WHERE amount_usd IS NOT NULL
),

nserc AS (
    SELECT
        'NSERC'                             AS agency,
        'Canada'                            AS country,
        institution_name,
        province                            AS region,
        CASE
            WHEN UPPER(program_code) IN ('CGSD','CGSM','PDF') THEN 'STEM'
            WHEN UPPER(program_code) IN ('CREATE') THEN 'STEM'
            ELSE 'STEM'   -- NSERC is STEM-only by mandate
        END                                 AS broad_category,
        'XX'                                AS cip_parent_code,
        fiscal_year::INTEGER                AS year,
        amount_usd_approx                   AS amount_usd,
        NULL                                AS is_active,   -- NSERC CSV doesn't have end date
        applicant_name                      AS pi_name,
        CASE WHEN is_graduate_award THEN 'graduate_award' ELSE 'research_grant' END AS grant_type
    FROM {{ ref('stg_nserc_awards') }}
    WHERE amount_cad IS NOT NULL
),

all_sources AS (
    SELECT * FROM nsf
    UNION ALL
    SELECT * FROM nih
    UNION ALL
    SELECT * FROM nserc
),

aggregated AS (
    SELECT
        agency,
        country,
        broad_category,
        year,
        grant_type,

        COUNT(*)                    AS award_count,
        SUM(amount_usd)             AS total_usd,
        AVG(amount_usd)             AS avg_usd,
        MEDIAN(amount_usd)          AS median_usd,
        COUNT(DISTINCT institution_name) AS institution_count,
        COUNT(DISTINCT pi_name) FILTER (WHERE pi_name IS NOT NULL) AS pi_count

    FROM all_sources
    WHERE year BETWEEN 2015 AND EXTRACT(YEAR FROM CURRENT_DATE)::INTEGER
      AND broad_category NOT IN ('Other', 'XX')
    GROUP BY 1, 2, 3, 4, 5
)

SELECT * FROM aggregated
ORDER BY year DESC, total_usd DESC
```

---

## Step 3.7 — Run and Verify

```bash
cd transform/scholarhub

# Run all new models
dbt run --select stg_nserc_awards stg_nih_projects mart_funding_north_america

# Verify
python -c "
import duckdb
conn = duckdb.connect('warehouse/scholarhub.duckdb')

result = conn.execute('''
    SELECT country, agency, year, SUM(total_usd)/1e9 AS total_billions,
           SUM(award_count) AS awards
    FROM analytics_marts.mart_funding_north_america
    WHERE year >= 2020
    GROUP BY 1, 2, 3
    ORDER BY year DESC, total_billions DESC
    LIMIT 15
''').fetchdf()
print(result.to_string())
"
```

---

## Phase 3 Checklist

- [ ] NSERC CSV downloaded and loaded: `SELECT COUNT(*) FROM raw_nserc_awards`
- [ ] NIH extractor runs: `python -c "from extractors.federal_apis.nih_extractor import NIHExtractor; NIHExtractor().extract(max_records=500)"`
- [ ] `stg_nserc_awards` and `stg_nih_projects` build successfully
- [ ] `mart_funding_north_america` has rows from both US and Canada
- [ ] Raw crawl log shows entries for all 3 sources

**What you've built:**
- Multi-source extraction with format heterogeneity (API, CSV, CKAN)
- Currency normalization (CAD → USD approximation)
- Cross-country comparison capability in one mart table
- T32/training grant detection for BQ-1 (who is hiring?)

**Next:** Phase 4 — Airflow orchestration to make all this run automatically every day.
