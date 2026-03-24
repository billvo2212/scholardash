# tests/unit/test_nsf_extractor.py
import pytest
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from extractors.federal_apis.nsf_extractor import NSFExtractor
from extractors.utils.rate_limiter import RateLimiter


@pytest.fixture
def sample_nsf_data():
    """Load sample NSF API response from fixtures."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "nsf_sample.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def nsf_extractor():
    """Create NSFExtractor instance for testing."""
    return NSFExtractor()


@pytest.fixture
def mock_db_connection(monkeypatch):
    """Mock database connection."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (1,)  # next ID

    def mock_get_connection(read_only=False):
        return mock_conn

    monkeypatch.setattr("extractors.base.get_connection", mock_get_connection)
    return mock_conn


class TestQualityScoring:
    """Test data quality scoring logic."""

    def test_quality_score_complete_record(self, nsf_extractor, sample_nsf_data):
        """Complete record with all fields should score 1.0."""
        award = sample_nsf_data["response"]["award"][0]
        score = nsf_extractor.calculate_quality_score(award)
        assert score == 1.0, f"Complete record should score 1.0, got {score}"

    def test_quality_score_partial_record(self, nsf_extractor):
        """Partial record with missing fields should score < 1.0."""
        award = {
            "id": "12345",
            "title": "Test Award",
            "piFirstName": "John",
            "piLastName": "Doe",
            # Missing: location, funding, abstract, dates
        }
        score = nsf_extractor.calculate_quality_score(award)
        assert 0.0 < score < 1.0, f"Partial record should score between 0 and 1, got {score}"
        assert score == 0.3, f"Record with only PI name should score 0.3, got {score}"

    def test_quality_score_minimal_record(self, nsf_extractor):
        """Minimal record with only ID should score 0.0."""
        award = {"id": "12345"}
        score = nsf_extractor.calculate_quality_score(award)
        assert score == 0.0, f"Minimal record should score 0.0, got {score}"

    def test_quality_score_no_pi_first_last(self, nsf_extractor):
        """Record with pdPIName but no separate first/last should get partial credit."""
        award = {
            "id": "12345",
            "pdPIName": "John Doe",  # Combined name
            "fundsObligatedAmt": "100000"
        }
        score = nsf_extractor.calculate_quality_score(award)
        assert score == 0.4, f"Expected 0.4 (0.2 for partial PI + 0.2 for funding), got {score}"


class TestRateLimiter:
    """Test rate limiting functionality."""

    def test_rate_limiter_enforces_limit(self):
        """Rate limiter should enforce requests per minute limit."""
        import time
        limiter = RateLimiter(requests_per_minute=10)

        start = time.monotonic()
        # Make 11 requests (1 over limit)
        for _ in range(11):
            limiter.wait()
        elapsed = time.monotonic() - start

        # Should take at least 6 seconds (10 requests allowed in first minute,
        # 11th request must wait for window to slide)
        # Allow some tolerance for test execution overhead
        assert elapsed >= 5.5, f"Rate limiter should enforce delay, only took {elapsed}s"


class TestNSFExtractor:
    """Test NSF extractor functionality."""

    @patch('extractors.federal_apis.nsf_extractor.NSFExtractor.fetch_with_retry')
    def test_fetch_batch(self, mock_fetch, nsf_extractor, sample_nsf_data):
        """Test fetching a batch from NSF API."""
        mock_response = Mock()
        mock_response.json.return_value = sample_nsf_data
        mock_fetch.return_value = mock_response

        result = nsf_extractor.fetch_batch(offset=0, limit=25)

        assert "response" in result
        assert "award" in result["response"]
        assert len(result["response"]["award"]) == 5
        mock_fetch.assert_called_once()

    @patch('extractors.federal_apis.nsf_extractor.NSFExtractor.fetch_with_retry')
    def test_extract_with_mock_api(
        self,
        mock_fetch,
        nsf_extractor,
        sample_nsf_data,
        mock_db_connection,
        tmp_path,
        monkeypatch
    ):
        """Test full extraction with mocked API responses."""
        # Mock API response
        mock_response = Mock()
        mock_response.json.return_value = sample_nsf_data
        mock_fetch.return_value = mock_response

        # Mock settings to use temp directory
        from config import settings
        monkeypatch.setattr(settings, "raw_data_dir", tmp_path)

        # Run extraction
        result = nsf_extractor.extract(max_records=5)

        # Verify results
        assert result.source_name == "NSF Award Search API"
        assert result.records_found == 5
        assert result.records_loaded == 5
        assert result.records_failed == 0
        assert 0.0 <= result.quality_avg <= 1.0
        assert result.duration_secs > 0
        assert result.status == "SUCCESS"

        # Verify database calls
        assert mock_db_connection.execute.call_count > 0

    def test_save_to_file(self, nsf_extractor, sample_nsf_data, tmp_path, monkeypatch):
        """Test saving awards to JSON file."""
        from config import settings
        monkeypatch.setattr(settings, "raw_data_dir", tmp_path)

        awards = sample_nsf_data["response"]["award"]
        nsf_extractor.save_to_file(awards, batch_num=1)

        # Check file was created
        files = list(tmp_path.glob("**/batch_0001.json"))
        assert len(files) == 1, "Should create exactly one batch file"

        # Verify file contents
        with open(files[0]) as f:
            saved_data = json.load(f)
        assert len(saved_data) == 5
        assert saved_data[0]["id"] == "2154321"


class TestRetryLogic:
    """Test retry logic for HTTP requests."""

    @patch('extractors.base.requests.Session.get')
    def test_retry_on_server_error(self, mock_get, nsf_extractor):
        """Should retry on 5xx server errors."""
        from requests import HTTPError

        # First two calls fail with 503, third succeeds
        mock_response_fail = Mock()
        mock_response_fail.status_code = 503
        mock_response_fail.raise_for_status.side_effect = HTTPError(response=mock_response_fail)

        mock_response_success = Mock()
        mock_response_success.status_code = 200
        mock_response_success.raise_for_status.return_value = None

        mock_get.side_effect = [
            mock_response_fail,
            mock_response_fail,
            mock_response_success
        ]

        # Should succeed after retries
        response = nsf_extractor.fetch_with_retry("http://test.com")
        assert response.status_code == 200
        assert mock_get.call_count == 3

    @patch('extractors.base.requests.Session.get')
    def test_no_retry_on_client_error(self, mock_get, nsf_extractor):
        """Should NOT retry on 4xx client errors."""
        from requests import HTTPError

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = HTTPError(response=mock_response)
        mock_get.return_value = mock_response

        # Should raise immediately without retrying
        with pytest.raises(HTTPError):
            nsf_extractor.fetch_with_retry("http://test.com")

        assert mock_get.call_count == 1  # Only one attempt


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
