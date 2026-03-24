#!/usr/bin/env python3
# extractors/federal_apis/nih_extractor.py
"""
NIH RePORTER v2 API Extractor.

Fetches biomedical research project data from NIH RePORTER API and stores in raw_nih_projects table.

API Docs: https://api.reporter.nih.gov/

Usage:
    python -m extractors.federal_apis.nih_extractor
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from extractors.base import BaseExtractor, ExtractResult
from extractors.utils.rate_limiter import RateLimiter
from config.settings import settings
from tqdm import tqdm


class NIHExtractor(BaseExtractor):
    """
    Extractor for NIH RePORTER v2 API.

    API Endpoint: https://api.reporter.nih.gov/v2/projects/search
    Method: POST (with JSON body for search criteria)
    Rate Limit: 5 requests per minute (configurable)
    Pagination: offset and limit parameters
    """

    BASE_URL = "https://api.reporter.nih.gov/v2/projects/search"

    def __init__(self):
        super().__init__(source_name="NIH RePORTER v2 API")
        self.rate_limiter = RateLimiter(settings.nih_api_rate_limit_per_minute)

    def calculate_quality_score(self, project: dict) -> float:
        """
        Calculate data quality score from 0.0 to 1.0.

        Scoring criteria:
        - Has PI name (0.3)
        - Has organization (0.2)
        - Has funding amount (0.2)
        - Has abstract (0.2)
        - Has valid dates (0.1)
        """
        score = 0.0

        # PI name (0.3)
        contact_pi = project.get("contact_pi_name")
        if contact_pi:
            score += 0.3

        # Organization (0.2)
        org = project.get("organization")
        if org and org.get("org_name"):
            score += 0.2

        # Funding amount (0.2)
        award_amount = project.get("award_amount")
        if award_amount and award_amount > 0:
            score += 0.2

        # Abstract (0.2)
        abstract = project.get("abstract_text") or project.get("project_title")
        if abstract and len(abstract) > 100:
            score += 0.2

        # Valid dates (0.1)
        if project.get("project_start_date") and project.get("project_end_date"):
            score += 0.1

        return score

    def fetch_batch(
        self,
        offset: int = 0,
        limit: int = 500,
        fiscal_year: int = 2024
    ) -> dict:
        """
        Fetch one batch of projects from NIH API.

        Args:
            offset: Starting record index
            limit: Number of records to fetch (max 500)
            fiscal_year: Fiscal year to search

        Returns:
            API response as dict

        Raises:
            requests.HTTPError: On HTTP errors
        """
        self.rate_limiter.wait()

        # NIH API uses POST with JSON body
        search_criteria = {
            "criteria": {
                "fiscal_years": [fiscal_year],
            },
            "offset": offset,
            "limit": limit,
            "sort_field": "project_start_date",
            "sort_order": "desc"
        }

        self.logger.info(
            "fetching_nih_batch",
            offset=offset,
            limit=limit,
            fiscal_year=fiscal_year
        )

        response = self.fetch_with_retry(
            self.BASE_URL,
            method="POST",
            json_data=search_criteria
        )
        return response.json()

    def save_to_db(self, projects: list[dict], batch_num: int) -> tuple[int, int, float]:
        """
        Save projects to raw_nih_projects table.

        Args:
            projects: List of project records
            batch_num: Batch number for logging

        Returns:
            Tuple of (loaded_count, failed_count, avg_quality)
        """
        conn = self._get_db_connection()
        loaded = 0
        failed = 0
        quality_scores = []

        for project in projects:
            try:
                quality_score = self.calculate_quality_score(project)
                quality_scores.append(quality_score)

                # Get next ID
                result = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM raw_nih_projects").fetchone()
                next_id = result[0]

                # Insert record
                conn.execute("""
                    INSERT INTO raw_nih_projects (id, extracted_at, response_json, quality_score, extraction_metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    next_id,
                    datetime.now(timezone.utc),
                    json.dumps(project),
                    quality_score,
                    json.dumps({"batch_num": batch_num, "project_num": project.get("project_num")})
                ))
                loaded += 1

            except Exception as e:
                self.logger.error("record_insert_failed", project_num=project.get("project_num"), error=str(e))
                failed += 1

        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

        self.logger.info(
            "batch_saved",
            batch_num=batch_num,
            loaded=loaded,
            failed=failed,
            avg_quality=round(avg_quality, 3)
        )

        return loaded, failed, avg_quality

    def save_to_file(self, projects: list[dict], batch_num: int, fiscal_year: int):
        """
        Archive raw JSON to file system.

        Args:
            projects: List of project records
            batch_num: Batch number for filename
            fiscal_year: Fiscal year for directory organization
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_dir = settings.raw_data_dir / "nih" / str(fiscal_year) / date_str
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"batch_{batch_num:04d}.json"
        with open(output_file, "w") as f:
            json.dump(projects, f, indent=2)

        self.logger.info("batch_archived", file=str(output_file), records=len(projects))

    def extract(
        self,
        max_records: Optional[int] = 500,
        fiscal_year: int = 2024,
        start_offset: int = 0
    ) -> ExtractResult:
        """
        Extract NIH projects and load into raw_nih_projects table.

        Args:
            max_records: Maximum number of records to fetch (None = all available)
            fiscal_year: Fiscal year to extract
            start_offset: Starting offset for pagination

        Returns:
            ExtractResult with extraction metrics
        """
        start_time = time.time()
        total_loaded = 0
        total_failed = 0
        total_found = 0
        quality_scores = []
        errors = []
        batch_num = 0

        self.logger.info(
            "extraction_started",
            source=self.source_name,
            max_records=max_records,
            fiscal_year=fiscal_year,
            start_offset=start_offset
        )

        try:
            offset = start_offset
            batch_size = 500  # NIH API max

            # Create progress bar
            pbar = tqdm(
                total=max_records if max_records else 1000,
                desc=f"Extracting NIH projects (FY{fiscal_year})",
                unit="records"
            )

            while True:
                # Stop if we've reached max_records
                if max_records and total_loaded >= max_records:
                    break

                try:
                    # Fetch batch
                    response_data = self.fetch_batch(
                        offset=offset,
                        limit=batch_size,
                        fiscal_year=fiscal_year
                    )
                    projects = response_data.get("results", [])

                    # No more records
                    if not projects:
                        self.logger.info("no_more_records", offset=offset)
                        break

                    batch_num += 1
                    total_found += len(projects)

                    # Truncate if exceeds max_records
                    if max_records and total_loaded + len(projects) > max_records:
                        projects = projects[:max_records - total_loaded]

                    # Save to database
                    loaded, failed, avg_quality = self.save_to_db(projects, batch_num)
                    total_loaded += loaded
                    total_failed += failed
                    quality_scores.append(avg_quality)

                    # Save to file
                    self.save_to_file(projects, batch_num, fiscal_year)

                    # Update progress bar
                    pbar.update(len(projects))

                    offset += batch_size

                except Exception as e:
                    error_msg = f"Batch {batch_num} failed: {str(e)}"
                    self.logger.error("batch_extraction_failed", batch_num=batch_num, error=str(e))
                    errors.append(error_msg)
                    # Continue to next batch despite error

            pbar.close()

        except Exception as e:
            error_msg = f"Extraction failed: {str(e)}"
            self.logger.error("extraction_failed", error=str(e))
            errors.append(error_msg)

        duration = time.time() - start_time
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

        result = ExtractResult(
            source_name=self.source_name,
            records_found=total_found,
            records_loaded=total_loaded,
            records_failed=total_failed,
            quality_avg=avg_quality,
            duration_secs=duration,
            errors=errors
        )

        self.logger.info("extraction_complete", **result.to_dict())
        return result


def main():
    """CLI entry point."""
    print("🚀 Starting NIH project extraction...\n")

    with NIHExtractor() as extractor:
        result = extractor.extract(max_records=500, fiscal_year=2024)

    print(f"\n✅ Extraction complete!")
    print(f"📊 Results:")
    print(f"   - Records found: {result.records_found}")
    print(f"   - Records loaded: {result.records_loaded}")
    print(f"   - Records failed: {result.records_failed}")
    print(f"   - Avg quality score: {result.quality_avg:.3f}")
    print(f"   - Duration: {result.duration_secs:.2f}s")
    print(f"   - Status: {result.status}")

    if result.errors:
        print(f"\n⚠️  Errors ({len(result.errors)}):")
        for error in result.errors[:5]:
            print(f"   - {error}")


if __name__ == "__main__":
    main()
