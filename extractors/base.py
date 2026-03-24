# extractors/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging
import requests

from extractors.utils.db_connection import get_connection
from extractors.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ExtractResult:
    """
    Returned by every extractor's extract() method.

    Provides standardized metrics for pipeline observability.
    """
    source_name: str
    records_found: int
    records_loaded: int
    records_failed: int
    quality_avg: float
    duration_secs: float
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Compute status based on results."""
        if self.records_failed == 0:
            return "SUCCESS"
        elif self.records_loaded > 0:
            return "PARTIAL"
        else:
            return "FAILED"

    def to_dict(self) -> dict:
        """Convert to dict for logging."""
        return {
            "source_name": self.source_name,
            "records_found": self.records_found,
            "records_loaded": self.records_loaded,
            "records_failed": self.records_failed,
            "quality_avg": round(self.quality_avg, 3),
            "duration_secs": round(self.duration_secs, 2),
            "status": self.status,
            "error_count": len(self.errors)
        }


class BaseExtractor(ABC):
    """
    Base class for all extractors.

    All extractors (NSF, NIH, NSERC, university scrapers) must inherit
    from this class and implement the extract() method.

    This ensures:
    - Consistent logging and error handling
    - Standardized return format (ExtractResult)
    - Built-in retry logic for HTTP requests
    - Connection management
    """

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.logger = get_logger(self.__class__.__name__)
        self.session = requests.Session()  # Connection pooling

    @abstractmethod
    def extract(self, **params) -> ExtractResult:
        """
        Fetch data from source and load into raw_* table.

        This method must be implemented by all subclasses.

        Args:
            **params: Extractor-specific parameters (e.g., date ranges, filters)

        Returns:
            ExtractResult with metrics

        Raises:
            Exception: If extraction fails completely
        """
        pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def fetch_with_retry(
        self,
        url: str,
        method: str = "GET",
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        timeout: int = 30
    ) -> requests.Response:
        """
        HTTP request with exponential backoff retry.

        Retries on network errors and 5xx status codes.
        Fails immediately on 4xx client errors (bad request, not found, etc).

        Args:
            url: Request URL
            method: HTTP method (GET, POST, etc)
            params: Query parameters
            json_data: JSON body for POST requests
            timeout: Request timeout in seconds

        Returns:
            Response object

        Raises:
            requests.HTTPError: On 4xx errors (no retry)
            requests.RequestException: On network errors (after retries exhausted)
        """
        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=json_data, params=params, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Raise for 4xx/5xx errors
            response.raise_for_status()
            return response

        except requests.HTTPError as e:
            # 4xx errors: don't retry (client error, won't succeed on retry)
            if 400 <= e.response.status_code < 500:
                self.logger.error("http_client_error", url=url, status_code=e.response.status_code)
                raise
            # 5xx errors: retry (server error, might be temporary)
            self.logger.warning("http_server_error_retrying", url=url, status_code=e.response.status_code)
            raise

    def _get_db_connection(self):
        """Get DuckDB connection (for subclasses to use)."""
        return get_connection()

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close session on exit."""
        self.session.close()
