#!/usr/bin/env python3
# extractors/federal_apis/nsf_extractor.py
"""
NSF Award Search API Extractor.

Fetches grant data from NSF Award Search API and stores in raw_nsf_awards table.

API Docs: https://www.research.gov/common/webapi/awardapisearch-v1.htm

Usage:
    python -m extractors.federal_apis.nsf_extractor
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


class NSFExtractor(BaseExtractor):
    """
    Extractor for NSF Award Search API.

    API Endpoint: https://api.nsf.gov/services/v1/awards.json
    Rate Limit: 10 requests per minute (configurable)
    Pagination: 25 records per page (offset parameter)
    """

    BASE_URL = "https://api.nsf.gov/services/v1/awards.json"

    # Fields to request from API
    PRINT_FIELDS = [
        "id",
        "title",
        "piFirstName",
        "piLastName",
        "pdPIName",
        "perfLocation",
        "perfCity",
        "perfState",
        "perfZip",
        "perfCountry",
        "startDate",
        "expDate",
        "fundsObligatedAmt",
        "abstractText",
        "fundProgramName",
        "primaryProgram",
        "agency",
        "awardeeCity",
        "awardeeStateCode",
        "awardeeCountryCode"
    ]

    def __init__(self):
        super().__init__(source_name="NSF Award Search API")
        self.rate_limiter = RateLimiter(settings.nsf_api_rate_limit_per_minute)

    def calculate_quality_score(self, award: dict) -> float:
        """
        Calculate data quality score from 0.0 to 1.0.

        Scoring criteria:
        - Has PI name (0.3)
        - Has institution/location (0.2)
        - Has funding amount (0.2)
        - Has abstract (0.2)
        - Has valid dates (0.1)
        """
        score = 0.0

        # PI name (0.3)
        if award.get("piFirstName") and award.get("piLastName"):
            score += 0.3
        elif award.get("pdPIName"):
            score += 0.2  # Partial credit if only combined name

        # Institution/Location (0.2)
        if award.get("perfLocation") or (award.get("perfCity") and award.get("perfState")):
            score += 0.2

        # Funding amount (0.2)
        if award.get("fundsObligatedAmt") and float(award.get("fundsObligatedAmt", 0)) > 0:
            score += 0.2

        # Abstract (0.2)
        if award.get("abstractText") and len(award.get("abstractText", "")) > 100:
            score += 0.2

        # Valid dates (0.1)
        if award.get("startDate") and award.get("expDate"):
            score += 0.1

        return score

    def fetch_batch(self, offset: int = 0, date_start: str = "01/01/2020") -> dict:
        """
        Fetch one batch of awards from NSF API.

        Args:
            offset: Starting record index
            date_start: Start date for award search (MM/DD/YYYY format)

        Returns:
            API response as dict (returns 25 records per page by default)

        Raises:
            requests.HTTPError: On HTTP errors
        """
        self.rate_limiter.wait()

        params = {
            "printFields": ",".join(self.PRINT_FIELDS),
            "offset": offset,
            "dateStart": date_start,  # Required: NSF API needs at least one search parameter
            "agency": "NSF"
        }

        self.logger.info(
            "fetching_nsf_batch",
            offset=offset,
            date_start=date_start
        )

        response = self.fetch_with_retry(self.BASE_URL, params=params)
        return response.json()

    def save_to_db(self, awards: list[dict], batch_num: int) -> tuple[int, int, float]:
        """
        Save awards to raw_nsf_awards table.

        Args:
            awards: List of award records
            batch_num: Batch number for logging

        Returns:
            Tuple of (loaded_count, failed_count, avg_quality)
        """
        conn = self._get_db_connection()
        loaded = 0
        failed = 0
        quality_scores = []

        for award in awards:
            try:
                quality_score = self.calculate_quality_score(award)
                quality_scores.append(quality_score)

                # Get next ID
                result = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM raw_nsf_awards").fetchone()
                next_id = result[0]

                # Insert record
                conn.execute("""
                    INSERT INTO raw_nsf_awards (id, extracted_at, response_json, quality_score, extraction_metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    next_id,
                    datetime.now(timezone.utc),
                    json.dumps(award),
                    quality_score,
                    json.dumps({"batch_num": batch_num, "award_id": award.get("id")})
                ))
                loaded += 1

            except Exception as e:
                self.logger.error("record_insert_failed", award_id=award.get("id"), error=str(e))
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

    def save_to_file(self, awards: list[dict], batch_num: int):
        """
        Archive raw JSON to file system.

        Args:
            awards: List of award records
            batch_num: Batch number for filename
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_dir = settings.raw_data_dir / "nsf" / date_str
        output_dir.mkdir(parents=True, exist_ok=True)

        output_file = output_dir / f"batch_{batch_num:04d}.json"
        with open(output_file, "w") as f:
            json.dump(awards, f, indent=2)

        self.logger.info("batch_archived", file=str(output_file), records=len(awards))

    def extract(
        self,
        max_records: Optional[int] = 500,
        start_offset: int = 0
    ) -> ExtractResult:
        """
        Extract NSF awards and load into raw_nsf_awards table.

        Args:
            max_records: Maximum number of records to fetch (None = all available)
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
            start_offset=start_offset
        )

        try:
            offset = start_offset
            batch_size = 25  # NSF API returns 25 per page

            # Note: NSF API doesn't provide total count in response, so we'll paginate until empty
            # (removed initial probe batch since it's not needed)

            # Create progress bar
            pbar = tqdm(
                total=max_records if max_records else 1000,  # Estimate if no max
                desc="Extracting NSF awards",
                unit="records"
            )

            while True:
                # Stop if we've reached max_records
                if max_records and total_loaded >= max_records:
                    break

                try:
                    # Fetch batch (NSF API returns 25 records per page by default)
                    response_data = self.fetch_batch(offset=offset)
                    awards = response_data.get("response", {}).get("award", [])

                    # No more records
                    if not awards:
                        self.logger.info("no_more_records", offset=offset)
                        break

                    batch_num += 1
                    total_found += len(awards)

                    # Truncate if exceeds max_records
                    if max_records and total_loaded + len(awards) > max_records:
                        awards = awards[:max_records - total_loaded]

                    # Save to database
                    loaded, failed, avg_quality = self.save_to_db(awards, batch_num)
                    total_loaded += loaded
                    total_failed += failed
                    quality_scores.append(avg_quality)

                    # Save to file
                    self.save_to_file(awards, batch_num)

                    # Update progress bar
                    pbar.update(len(awards))

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
    print("🚀 Starting NSF Award extraction...\n")

    with NSFExtractor() as extractor:
        result = extractor.extract(max_records=500)

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
        for error in result.errors[:5]:  # Show first 5
            print(f"   - {error}")


if __name__ == "__main__":
    main()
