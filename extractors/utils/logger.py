# extractors/utils/logger.py
import structlog
import logging
from config.settings import settings


def get_logger(name: str):
    """
    Returns a structured logger.

    Why structlog? JSON-formatted logs are parseable by Airflow and log aggregators.
    Plain print() statements disappear into noise when you have 10 extractors running.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        A configured structlog logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("extraction_started", source="nsf", records=100)
        {"event": "extraction_started", "source": "nsf", "records": 100, "timestamp": "2026-03-19T10:00:00Z"}
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
