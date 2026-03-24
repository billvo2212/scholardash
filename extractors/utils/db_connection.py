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

    Args:
        read_only: If True, opens connection in read-only mode (allows concurrent readers)

    Returns:
        DuckDB connection instance

    Example:
        >>> conn = get_connection()
        >>> result = conn.execute("SELECT COUNT(*) FROM raw_nsf_awards").fetchone()
        >>> print(result[0])
        500
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
    """
    Close the DuckDB connection.

    Call this at the end of a process / Airflow task to release the lock.
    """
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("duckdb_connection_closed")
