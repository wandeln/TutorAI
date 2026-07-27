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


def get_session():
    """Yield a DB session. Use as FastAPI dependency: `Depends(get_session)`"""
    with Session(engine) as session:
        yield session