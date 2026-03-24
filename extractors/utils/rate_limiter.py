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

    This implementation tracks request timestamps in a sliding window
    and blocks when the limit would be exceeded.

    Args:
        requests_per_minute: Maximum number of requests allowed per minute

    Example:
        >>> limiter = RateLimiter(requests_per_minute=10)
        >>> for item in items:
        ...     limiter.wait()  # Blocks if limit would be exceeded
        ...     response = requests.get(url)
    """

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute
        self.timestamps: deque = deque()
        self.lock = Lock()

    def wait(self):
        """
        Block until it's safe to make the next request.

        Uses a sliding window approach: tracks timestamps of requests
        within the last 60 seconds and enforces the per-minute limit.
        """
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
