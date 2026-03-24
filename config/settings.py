# config/settings.py
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Uses Pydantic BaseSettings for type-safe configuration management.
    All settings can be overridden via .env file or environment variables.
    """

    # Warehouse
    duckdb_path: str = "warehouse/scholarhub.duckdb"

    # Rate limits (requests per minute)
    nsf_api_rate_limit_per_minute: int = 10
    nih_api_rate_limit_per_minute: int = 5

    # Paths
    raw_data_dir: Path = Path("data/raw")
    exports_dir: Path = Path("data/exports")

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Allow extra fields in .env (e.g., AIRFLOW_UID for Docker)


# Singleton instance — import this everywhere
settings = Settings()
