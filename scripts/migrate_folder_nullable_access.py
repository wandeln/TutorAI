#!/usr/bin/env python3
"""
One-Shot-Migration: ``task_workspace_folders.access`` nullable machen
(NULL = explizit „edit“) + Dedup doppelter (task_id, path)-Zeilen.

Hintergrund: Seit dem Ordner-Persistenz-Feature (folderApi) bedeutet eine
Zeile „expliziter Ordner“ — auch mit ``access=NULL`` (edit). Die Spalte war
historisch NOT NULL (Zeilen gab es nur für readonly/hidden) → INSERT/UPDATE
mit NULL würde sonst crashen (z. B. ``create_task_folder``,
``set_folder_access(None)``).

Teil 1 — Dedup (defensiv, idempotent): Doppelte Zeilen pro (task_id, path)
auf die höchste id reduzieren. Der Unique-Constraint
``uq_ws_folder_task_path`` verhindert neue Duplikate; ältere Relikte
(Race vor Anlage des Constraints) wären damit bereinigt.

Teil 2 — nullable access (idempotent, nur wenn nötig):
- SQLite: Tabellen-Rebuild (CREATE neu / COPY / DROP / RENAME) mit
  ``access VARCHAR(16)`` (nullable) + Indexe/Constraint nachlegen
  (DDL ist in SQLite transaktional — alles-or-nichts).
- PostgreSQL: ``ALTER COLUMN access DROP NOT NULL``.

Aufruf (im AICampus-Container, damit Code + DB zusammenpassen):
    sudo docker compose -f deploy/compose.local.yml -f deploy/compose.dev.yml \\
        exec aicampus timeout 120 python -m scripts.migrate_folder_nullable_access --dry-run
    sudo docker compose -f deploy/compose.local.yml -f deploy/compose.dev.yml \\
        exec aicampus timeout 120 python -m scripts.migrate_folder_nullable_access
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from database.base import create_db_and_tables, engine, migrate_schema  # noqa: E402
from database.models import TaskWorkspaceFolder  # noqa: E402
from sqlmodel import Session, select  # noqa: E402


def _access_is_not_null(conn) -> bool:
    """True, wenn die access-Spalte aktuell NOT NULL ist."""
    if engine.dialect.name == "postgresql":
        row = conn.exec_driver_sql("""
            SELECT a.attnotnull FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = 'task_workspace_folders'
              AND a.attname = 'access'
        """).fetchone()
        return bool(row and row[0])
    rows = conn.exec_driver_sql(
        "PRAGMA table_info(task_workspace_folders)").fetchall()
    # Zeilen: (cid, name, type, notnull, dflt_value, pk)
    return any(r[1] == "access" and int(r[3]) for r in rows)


def _dedup(session, dry_run: bool) -> int:
    """Teil 1: doppelte (task_id, path)-Zeilen bereinigen (höchste id bleibt)."""
    by_key: dict = {}
    for r in session.exec(select(TaskWorkspaceFolder)).all():
        by_key.setdefault((r.task_id, r.path), []).append(r)
    victims = [r for rows in by_key.values() if len(rows) > 1
               for r in sorted(rows, key=lambda x: x.id)[:-1]]
    for r in victims:
        print(f"Task {r.task_id} {r.path!r}: Duplikat id={r.id} "
              f"(access={r.access!r}) → löschen")
    if victims and not dry_run:
        for r in victims:
            session.delete(r)
        session.commit()
    return len(victims)


def _make_nullable(conn, dry_run: bool) -> bool:
    """Teil 2: access-Spalte nullable machen. True, wenn etwas geändert
    (bzw. im dry-run geändert WÜRDE) wurde."""
    if not _access_is_not_null(conn):
        return False
    print("access-Spalte ist NOT NULL → nullable machen")
    if dry_run:
        print("  [dry-run] nichts geschrieben")
        return True
    if engine.dialect.name == "postgresql":
        conn.exec_driver_sql(
            "ALTER TABLE task_workspace_folders "
            "ALTER COLUMN access DROP NOT NULL")
    else:
        # SQLite: Tabellen-Rebuild (keine direkte Nullability-Änderung).
        conn.exec_driver_sql("""
            CREATE TABLE task_workspace_folders_new (
                id INTEGER NOT NULL PRIMARY KEY,
                task_id INTEGER NOT NULL REFERENCES tasks(id),
                path VARCHAR(500) NOT NULL,
                access VARCHAR(16),
                updated_at DATETIME NOT NULL
            )""")
        conn.exec_driver_sql("""
            INSERT INTO task_workspace_folders_new
                (id, task_id, path, access, updated_at)
            SELECT id, task_id, path, access, updated_at
            FROM task_workspace_folders""")
        conn.exec_driver_sql("DROP TABLE task_workspace_folders")
        conn.exec_driver_sql(
            "ALTER TABLE task_workspace_folders_new "
            "RENAME TO task_workspace_folders")
        # DROP TABLE hat Index/Constraint mitgelöscht → neu anlegen:
        conn.exec_driver_sql(
            "CREATE INDEX ix_task_workspace_folders_task_id "
            "ON task_workspace_folders (task_id)")
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_ws_folder_task_path "
            "ON task_workspace_folders (task_id, path)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nur anzeigen, was migriert würde — nichts ändern")
    args = parser.parse_args()

    create_db_and_tables()
    migrate_schema()

    with Session(engine) as session:
        removed = _dedup(session, args.dry_run)
    with engine.begin() as conn:
        rebuilt = _make_nullable(conn, args.dry_run)

    if args.dry_run:
        print("\n[dry-run] keine Änderungen vorgenommen.")
    else:
        print(f"\nFertig: {removed} Duplikat-Zeile(n) entfernt, "
              f"nullable-Rebuild: {'ja' if rebuilt else 'nicht nötig'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
