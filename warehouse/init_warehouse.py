#!/usr/bin/env python3
# warehouse/init_warehouse.py
"""
Initialize DuckDB warehouse with schema for all zones.

Run this script once to create the database file and all necessary tables.

Usage:
    python warehouse/init_warehouse.py
"""

from pathlib import Path
import sys

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.utils.db_connection import get_connection, close_connection
from extractors.utils.logger import get_logger

logger = get_logger(__name__)


def init_raw_zone(conn):
    """Create raw zone tables (immutable, append-only)."""
    logger.info("creating_raw_zone_tables")

    # NSF Awards raw table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_nsf_awards (
            id INTEGER PRIMARY KEY,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            response_json TEXT NOT NULL,
            quality_score REAL,
            extraction_metadata JSON
        )
    """)
    logger.info("table_created", table="raw_nsf_awards")

    # NIH Projects raw table (Phase 3)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_nih_projects (
            id INTEGER PRIMARY KEY,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            response_json TEXT NOT NULL,
            quality_score REAL,
            extraction_metadata JSON
        )
    """)
    logger.info("table_created", table="raw_nih_projects")


def init_dimension_tables(conn):
    """Create dimension tables with seed data."""
    logger.info("creating_dimension_tables")

    # dim_date: Date spine from 2015-01-01 to 2030-12-31
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_date (
            date_key INTEGER PRIMARY KEY,
            date_value DATE NOT NULL,
            year INTEGER,
            quarter INTEGER,
            month INTEGER,
            month_name VARCHAR,
            day INTEGER,
            day_of_week INTEGER,
            day_name VARCHAR,
            is_weekend BOOLEAN,
            fiscal_year INTEGER
        )
    """)

    # Seed dim_date if empty
    count = conn.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0]
    if count == 0:
        logger.info("seeding_dim_date", start="2015-01-01", end="2030-12-31")
        conn.execute("""
            INSERT INTO dim_date
            SELECT
                CAST(strftime(d, '%Y%m%d') AS INTEGER) as date_key,
                d as date_value,
                EXTRACT(YEAR FROM d) as year,
                EXTRACT(QUARTER FROM d) as quarter,
                EXTRACT(MONTH FROM d) as month,
                strftime(d, '%B') as month_name,
                EXTRACT(DAY FROM d) as day,
                EXTRACT(DOW FROM d) as day_of_week,
                strftime(d, '%A') as day_name,
                CASE WHEN EXTRACT(DOW FROM d) IN (0, 6) THEN TRUE ELSE FALSE END as is_weekend,
                CASE
                    WHEN EXTRACT(MONTH FROM d) >= 10 THEN EXTRACT(YEAR FROM d) + 1
                    ELSE EXTRACT(YEAR FROM d)
                END as fiscal_year
            FROM generate_series(
                DATE '2015-01-01',
                DATE '2030-12-31',
                INTERVAL 1 DAY
            ) as t(d)
        """)
        logger.info("dim_date_seeded", rows=conn.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0])

    # dim_funding_agency
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_funding_agency (
            agency_id INTEGER PRIMARY KEY,
            agency_code VARCHAR NOT NULL,
            agency_name VARCHAR NOT NULL,
            country VARCHAR NOT NULL,
            agency_type VARCHAR,
            website_url VARCHAR,
            api_endpoint VARCHAR,
            tier INTEGER,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)

    # Seed funding agencies
    count = conn.execute("SELECT COUNT(*) FROM dim_funding_agency").fetchone()[0]
    if count == 0:
        logger.info("seeding_dim_funding_agency")
        conn.execute("""
            INSERT INTO dim_funding_agency VALUES
            (1, 'NSF', 'National Science Foundation', 'USA', 'Federal', 'https://www.nsf.gov', 'https://api.nsf.gov/services/v1/awards.json', 1, TRUE),
            (2, 'NIH', 'National Institutes of Health', 'USA', 'Federal', 'https://www.nih.gov', 'https://api.reporter.nih.gov/v2/projects/search', 1, TRUE),
            (3, 'NSERC', 'Natural Sciences and Engineering Research Council', 'Canada', 'Federal', 'https://www.nserc-crsng.gc.ca', NULL, 2, TRUE),
            (4, 'CIHR', 'Canadian Institutes of Health Research', 'Canada', 'Federal', 'https://cihr-irsc.gc.ca', NULL, 2, TRUE),
            (5, 'SSHRC', 'Social Sciences and Humanities Research Council', 'Canada', 'Federal', 'https://www.sshrc-crsh.gc.ca', NULL, 2, TRUE)
        """)
        logger.info("dim_funding_agency_seeded", rows=5)

    # dim_source: Data source registry
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_source (
            source_id INTEGER PRIMARY KEY,
            source_name VARCHAR NOT NULL,
            source_type VARCHAR NOT NULL,
            tier INTEGER,
            last_extracted_at TIMESTAMP,
            total_records_extracted INTEGER DEFAULT 0,
            avg_quality_score REAL,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)

    # Seed sources
    count = conn.execute("SELECT COUNT(*) FROM dim_source").fetchone()[0]
    if count == 0:
        logger.info("seeding_dim_source")
        conn.execute("""
            INSERT INTO dim_source (source_id, source_name, source_type, tier, is_active) VALUES
            (1, 'NSF Award Search API', 'API', 1, TRUE),
            (2, 'NIH RePORTER v2 API', 'API', 1, TRUE),
            (3, 'NSERC Awards Database', 'Bulk CSV', 2, TRUE),
            (4, 'CIHR Open Data', 'Bulk CSV', 2, TRUE),
            (5, 'IPEDS', 'API', 3, FALSE),
            (6, 'Statistics Canada', 'API', 3, FALSE)
        """)
        logger.info("dim_source_seeded", rows=6)


def init_fact_tables(conn):
    """Create fact tables (will be populated by extractors and dbt)."""
    logger.info("creating_fact_tables")

    # fact_crawl_event: Pipeline observability
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fact_crawl_event (
            crawl_id INTEGER PRIMARY KEY,
            source_id INTEGER,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            records_found INTEGER,
            records_loaded INTEGER,
            records_failed INTEGER,
            avg_quality_score REAL,
            status VARCHAR,
            error_message TEXT,
            FOREIGN KEY (source_id) REFERENCES dim_source(source_id)
        )
    """)
    logger.info("table_created", table="fact_crawl_event")


def main():
    """Initialize DuckDB warehouse."""
    logger.info("warehouse_initialization_started")

    try:
        conn = get_connection()

        init_raw_zone(conn)
        init_dimension_tables(conn)
        init_fact_tables(conn)

        # Verify tables created
        tables = conn.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main'
            ORDER BY table_name
        """).fetchall()

        logger.info("warehouse_initialization_complete", tables=[t[0] for t in tables])
        print("\n✅ Warehouse initialized successfully!")
        print(f"📊 Tables created: {len(tables)}")
        for table in tables:
            print(f"   - {table[0]}")

    except Exception as e:
        logger.error("warehouse_initialization_failed", error=str(e))
        raise
    finally:
        close_connection()


if __name__ == "__main__":
    main()
