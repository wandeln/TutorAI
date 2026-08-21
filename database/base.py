"""
Database Engine + Session Management.

Provides SQLModel-compatible engine and session factory for dependency injection.
Supports both SQLite (dev) and PostgreSQL (production).
"""

from sqlmodel import SQLModel, create_engine, Session
from config import DATABASE_URL

# ─── Connection Pool Settings ────────────────────────────────────
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        echo=False,              # Set True for SQL-debug output
        connect_args={"check_same_thread": False},  # Required for SQLite + threads
    )
else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
    )


def create_db_and_tables():
    """Create all tables. Call once at app startup."""
    SQLModel.metadata.create_all(engine)


def migrate_schema():
    """Fügt fehlende Spalten an bestehenden Tabellen hinzu.

    create_all() legt nur fehlende Tabellen an, ergänzt aber keine neuen
    Spalten. Hier werden fehlende Spalten nachgezogen (SQLite + PostgreSQL).
    """
    new_global_settings_columns = {
        "llm_api_url_public": "VARCHAR",
        "llm_api_key_public": "VARCHAR",
        "llm_model_public": "VARCHAR",
    }

    with engine.begin() as conn:
        if "sqlite" in DATABASE_URL:
            rows = conn.exec_driver_sql("PRAGMA table_info(global_settings)").fetchall()
            if not rows:
                return
            existing = {row[1] for row in rows}
            for col, col_type in new_global_settings_columns.items():
                if col not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE global_settings ADD COLUMN {col} {col_type}"
                    )
        else:
            for col, col_type in new_global_settings_columns.items():
                conn.exec_driver_sql(
                    f"ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS {col} {col_type}"
                )


def get_session():
    """Yield a DB session. Use as FastAPI dependency: `Depends(get_session)`"""
    with Session(engine) as session:
        yield session