"""
Database Engine + Session Management.

Provides SQLModel-compatible engine and session factory for dependency injection.
Supports both SQLite (dev) and PostgreSQL (production).
"""

import re

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


def _sqlite_drop_column(conn, table: str, column: str) -> None:
    """Entfernt eine Spalte (SQLite).

    SQLite >= 3.35: ALTER TABLE ... DROP COLUMN; ältere Versionen:
    Tabellen-Rebuild (neue Tabelle anlegen, Daten kopieren, alte löschen).
    """
    try:
        conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        return
    except Exception:
        pass  # SQLite < 3.35 → Rebuild

    cols = [
        row[1]
        for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        if row[1] != column
    ]
    create_sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    # Die Spalten-Definition aus dem CREATE-TABLE-DDL entfernen. Zwei Fälle:
    # 1) eigene Zeile (so legt SQLAlchemy die Tabelle an)
    new_create = re.sub(
        rf"^[ \t]*{re.escape(column)}[ \t]+[^\n]*\n",
        "",
        create_sql,
        count=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    # 2) an die vorherige Zeile angehängt (so schreibt SQLite das DDL nach
    #    ALTER TABLE ... ADD COLUMN um)
    if re.search(rf"\b{re.escape(column)}\b", new_create, flags=re.IGNORECASE):
        new_create = re.sub(
            rf",\s*{re.escape(column)}[ \t]+[A-Za-z0-9_()+'\" ]*",
            "",
            new_create,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.search(rf"\b{re.escape(column)}\b", new_create, flags=re.IGNORECASE):
        raise RuntimeError(
            f"Migration: Spalte {table}.{column} konnte nicht aus dem CREATE-TABLE-DDL entfernt werden."
        )
    new_create = new_create.replace(",\n)", "\n)")
    new_table = f"{table}_migration_tmp"
    new_create = (
        new_create.replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{new_table}"', 1)
        .replace(f"CREATE TABLE {table}", f"CREATE TABLE {new_table}", 1)
    )
    quoted = ", ".join(f'"{c}"' for c in cols)
    conn.exec_driver_sql(new_create)
    conn.exec_driver_sql(f"INSERT INTO {new_table} ({quoted}) SELECT {quoted} FROM {table}")
    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {new_table} RENAME TO {table}")


def migrate_schema():
    """Migriert Schema-Änderungen an bestehenden Datenbanken.

    create_all() legt nur fehlende Tabellen an, ergänzt aber keine neuen
    Spalten bzw. entfernt keine entfallene. Hier werden fehlende Spalten
    nachgezogen und obsolete Spalten entfernt (SQLite + PostgreSQL).
    """
    column_migrations = {
        "users": {
            "avatar": "VARCHAR",  # relatives Pfad des Profilbilds (avatars/<uuid>.<ext>)
        },
        "global_settings": {
            "llm_api_url_public": "VARCHAR",
            "llm_api_key_public": "VARCHAR",
            "llm_model_public": "VARCHAR",
        },
        "course_script_sections": {
            "summary": "TEXT",  # Interne LLM-Zusammenfassung (nicht für Studenten)
        },
        "forum_messages": {
            "channel_id": "INTEGER",  # Forum-Kanal, dem die Nachricht zugeordnet ist
        },
    }
    column_drops = {
        "course_media": ["is_visible"],  # Sichtbarkeit steuert der einbindende Inhalt
    }
    is_sqlite = "sqlite" in DATABASE_URL

    with engine.begin() as conn:
        for table, new_columns in column_migrations.items():
            if is_sqlite:
                rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    continue
                existing = {row[1] for row in rows}
                for col, col_type in new_columns.items():
                    if col not in existing:
                        conn.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                        )
            else:
                for col, col_type in new_columns.items():
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}"
                    )

        for table, dropped in column_drops.items():
            if is_sqlite:
                rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    continue
                existing = {row[1] for row in rows}
                for col in dropped:
                    if col in existing:
                        _sqlite_drop_column(conn, table, col)
            else:
                for col in dropped:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}"
                    )


def get_session():
    """Yield a DB session. Use as FastAPI dependency: `Depends(get_session)`"""
    with Session(engine) as session:
        yield session