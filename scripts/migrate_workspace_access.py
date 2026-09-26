#!/usr/bin/env python3
"""
One-Shot-Migration: Workspace-Pfad-Konvention → explizite Zugriffsklassen.

Für alle Tasks mit task_type=workspace:

  DB (task_workspace_files.access, bisher alle NULL):
    - run.sh / init.sh (Wurzel)         → "readonly"
    - .solution/*  und  .tests/*        → "hidden"
    - init_private.sh (falls vorhanden) → "hidden"
    - Rest                              → bleibt NULL (= "edit")

  Neue Ordner-Rows (task_workspace_folders):
    - data/   → "readonly"  (falls Dateien unter data/ existieren)
    - tests/  → "readonly"  (falls Dateien unter tests/ existieren)

  Disk:
    - data/workspaces/{task}/.private/*  →  Task-Root
      (.private/.solution/* → .solution/*, .private/.tests/* → .tests/*)
      Danach wird .private/ entfernt. KRITISCH: die DB-Rows zeigen auf
      die Root-Pfade — ohne den Move wären alle 👤-Dateien „verloren".
    - init.sh: /assets/-Pfade → /workspace/ (neues Mount-Layout) +
      Checksum/Size-Update im DB-Row.

  Achtung: Das Skript ruft bewusst NICHT migrate_schema() auf — das würde
  die is_public-Spalte dropen, während die alte App noch läuft. Die
  benötigten Schema-Änderungen (access-Spalte, Ordner-Tabelle) werden
  hier idempotent per rohem DDL nachgezogen; der is_public-Drop passiert
  beim Neustart der neuen App automatisch.

Idempotenz-Guard (ohne --force):
  Abbruch, wenn die is_public-Spalte bereits fehlt (neues Schema aktiv)
  ODER bereits Ordner-Zeilen existieren (Migration schon gelaufen).
  Die Einzelschritte sind zusätzlich einzeln idempotent, damit ein
  --force-Lauf nichts zweimal verändert.

Aufruf (im AICampus-Container, damit Code + DB zusammenpassen — Reihenfolge:
ERST migrieren, DANN die neue App starten):
    sudo docker compose -f deploy/compose.local.yml build aicampus
    sudo docker compose -f deploy/compose.local.yml run --rm --no-deps \
        aicampus timeout 300 python -m scripts.migrate_workspace_access --dry-run
    sudo docker compose -f deploy/compose.local.yml run --rm --no-deps \
        aicampus timeout 300 python -m scripts.migrate_workspace_access
    sudo docker compose -f deploy/compose.local.yml up -d --build aicampus
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")

from database.base import create_db_and_tables, engine  # noqa: E402
from database.models import Task, TaskType, TaskWorkspaceFolder  # noqa: E402
from services.workspace_service import (  # noqa: E402
    file_disk_path,
    task_workspace_dir,
)
from sqlmodel import Session, select  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _column_exists(cur, table: str, col: str) -> bool:
    return any(r[1] == col
               for r in cur.execute(text(f"PRAGMA table_info({table})")))


def _table_exists(cur, table: str) -> bool:
    row = cur.execute(
        text("select name from sqlite_master where type='table' and name=:t"),
        {"t": table},
    ).fetchone()
    return row is not None


def _guard() -> None:
    """Idempotenz-Guard: Abbruch bei bereits migriertem Schema/Datenstand."""
    with engine.connect() as conn:
        has_is_public = _column_exists(conn, "task_workspace_files", "is_public")
        folder_rows = 0
        if _table_exists(conn, "task_workspace_folders"):
            folder_rows = conn.execute(
                text("select count(*) from task_workspace_folders")
            ).scalar() or 0
    if not has_is_public:
        raise SystemExit(
            "GUARD: is_public-Spalte fehlt — neues Schema bereits aktiv.\n"
            "Migration scheint gelaufen (oder App mit neuem Code lief bereits).\n"
            "Bei Bedarf mit --force neu ausführen.")
    if folder_rows:
        raise SystemExit(
            f"GUARD: {folder_rows} Ordner-Zeile(n) in task_workspace_folders —\n"
            "Migration scheint schon gelaufen. Bei Bedarf mit --force neu ausführen.")


def _ensure_schema(dry_run: bool) -> bool:
    """access-Spalte + Ordner-Tabelle idempotent nachziehen (ohne is_public-
    Drop). Liefert True, wenn die access-Spalte (nun) existiert."""
    with engine.connect() as conn:
        has_access = _column_exists(conn, "task_workspace_files", "access")
    if not has_access:
        if dry_run:
            print("  [dry-run] ALTER TABLE task_workspace_files ADD COLUMN access")
            return False
        with engine.begin() as conn:
            conn.execute(text(
                "alter table task_workspace_files add column access VARCHAR(16)"))
        print("  + Spalte task_workspace_files.access angelegt")
    with engine.connect() as conn:
        table_ok = _table_exists(conn, "task_workspace_folders")
    if not table_ok:
        if dry_run:
            print("  [dry-run] CREATE TABLE task_workspace_folders")
        else:
            create_db_and_tables()  # legt die fehlende Tabelle an
            print("  + Tabelle task_workspace_folders angelegt")
    return True


def _task_file_paths(task_id: int) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("select path from task_workspace_files "
                 "where task_id=:id order by id"),
            {"id": task_id},
        ).fetchall()
    return [r[0] for r in rows]


def _already_accessed(task_id: int, has_access: bool) -> set[str]:
    """Pfade, die bereits eine explizite Klasse haben (nur mit access-Spalte)."""
    if not has_access:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(
            text("select path from task_workspace_files "
                 "where task_id=:id and access is not null"),
            {"id": task_id},
        ).fetchall()
    return {r[0] for r in rows}


def _folder_row_exists(task_id: int, path: str) -> bool:
    with engine.connect() as conn:
        if not _table_exists(conn, "task_workspace_folders"):
            return False
        row = conn.execute(
            text("select 1 from task_workspace_folders "
                 "where task_id=:id and path=:p"),
            {"id": task_id, "p": path},
        ).fetchone()
    return row is not None


def _plan_disk_move(task_id: int) -> list[str]:
    """.private/* → Task-Root planen; Kollisionen stoppen den Task."""
    base = task_workspace_dir(task_id)
    priv = base / ".private"
    if not priv.is_dir():
        return []
    lines = []
    for entry in sorted(priv.iterdir()):
        dst = base / entry.name
        if dst.exists():
            lines.append(f"  ✗ KOLLISION: {dst.name} existiert bereits in der "
                         f"Root — Task manuell prüfen, nichts verschoben")
            return lines
        lines.append(f"  move .private/{entry.name} → {entry.name}/")
    return lines


def _do_disk_move(task_id: int) -> bool:
    """Disk-Move ausführen; False bei Kollision (nichts verschoben)."""
    base = task_workspace_dir(task_id)
    priv = base / ".private"
    for entry in sorted(priv.iterdir()):
        if (base / entry.name).exists():
            return False
    for entry in sorted(priv.iterdir()):
        os.replace(entry, base / entry.name)
    priv.rmdir()
    return True


def _init_rewriting_needed(task: Task) -> bool:
    p = file_disk_path(task.id, "init.sh")
    if not p.is_file():
        return False
    try:
        return "/assets/" in p.read_text("utf-8")
    except (UnicodeDecodeError, OSError):
        return False


def _do_init_rewriting(task: Task) -> None:
    p = file_disk_path(task.id, "init.sh")
    new = p.read_text("utf-8").replace("/assets/", "/workspace/")
    data = new.encode("utf-8")
    p.write_bytes(data)
    with engine.begin() as conn:
        conn.execute(
            text("update task_workspace_files set size=:s, checksum=:c "
                 "where task_id=:id and path='init.sh'"),
            {"s": len(data), "c": hashlib.sha256(data).hexdigest(),
             "id": task.id},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nur anzeigen, was migriert würde — nichts ändern")
    parser.add_argument("--force", action="store_true",
                        help="Guard überspringen (z. B. bei Teilauszuständen)")
    args = parser.parse_args()

    # Guard + Schema-Voraussetzungen (trotz --force: access-Spalte wird
    # nachgezogen, falls sie fehlt).
    if not args.force:
        _guard()
    has_access = _ensure_schema(args.dry_run)

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.task_type == TaskType.WORKSPACE)
        ).all()
        if not tasks:
            print("Keine Workspace-Tasks gefunden.")
            return 0

        for task in tasks:
            paths = _task_file_paths(task.id)
            print(f"Task {task.id} ({task.title!r}): {len(paths)} Datei-Row(s)")
            done = _already_accessed(task.id, has_access)

            # 1) Disk-Move .private/ → Root
            move_lines = _plan_disk_move(task.id)
            if move_lines:
                for line in move_lines:
                    print(line)
                if (not any("KOLLISION" in l for l in move_lines)
                        and not args.dry_run and _do_disk_move(task.id)):
                    print("  ✓ .private/ entfernt")

            # 2) access-Werte (nur noch NULL-Rows — Explizites bleibt erhalten)
            ro_files = [p for p in ("run.sh", "init.sh")
                        if p in paths and p not in done]
            hid_files = sorted(
                p for p in paths
                if p.startswith(".solution/") or p.startswith(".tests/")
                or p == "init_private.sh"
            )
            hid_files = [p for p in hid_files if p not in done]
            if ro_files:
                print(f"  access=readonly: {', '.join(ro_files)}")
            if hid_files:
                print(f"  access=hidden:   {', '.join(hid_files)}")
            if not args.dry_run and (ro_files or hid_files):
                with engine.begin() as conn:
                    for p in ro_files:
                        conn.execute(
                            text("update task_workspace_files "
                                 "set access='readonly' "
                                 "where task_id=:id and path=:p "
                                 "and access is null"),
                            {"id": task.id, "p": p})
                    for p in hid_files:
                        conn.execute(
                            text("update task_workspace_files "
                                 "set access='hidden' "
                                 "where task_id=:id and path=:p "
                                 "and access is null"),
                            {"id": task.id, "p": p})

            # 3) Ordner-Rows für data/ und tests/
            for folder in ("data", "tests"):
                if any(p.startswith(folder + "/") for p in paths) \
                        and not _folder_row_exists(task.id, folder):
                    print(f"  folder {folder}/ → readonly")
                    if not args.dry_run:
                        session.add(TaskWorkspaceFolder(
                            task_id=task.id, path=folder, access="readonly"))
                        session.commit()

            # 4) init.sh: /assets/ → /workspace/
            if _init_rewriting_needed(task):
                print("  rewrite init.sh: /assets/ → /workspace/ "
                      "(Checksum/Size werden aktualisiert)")
                if not args.dry_run:
                    _do_init_rewriting(task)

        if args.dry_run:
            print("\n[dry-run] keine Änderungen vorgenommen.")
        else:
            print("\nFertig. Nächster Schritt: neue App starten (dropt is_public):")
            print("  sudo docker compose -f deploy/compose.local.yml up -d --build aicampus")
            print("Danach je Task: 🔄 Sync ausführen — und falls init.sh existiert,")
            print("das alte Task-Image entfernen (neuer init-Hash) bzw. neu initialisieren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
