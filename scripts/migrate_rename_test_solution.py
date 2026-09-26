#!/usr/bin/env python3
"""
One-Shot-Migration: `.run_solution.sh` → `.test_solution.sh` (Rename) +
aktualisierung des Stub-Contents auf die robuste Version.

Teil 1 — Rename: Für alle Workspace-Tasks mit einer Datei
``.run_solution.sh`` werden Disk-Datei und DB-Zeile
(TaskWorkspaceFile.path) zu ``.test_solution.sh`` umbenannt.

Teil 2 — Stub-Content: ``.test_solution.sh``-Dateien, deren Inhalt eine
bekannte ältere Standard-Variante ist (ersetzt z. B. die fragile
`set -e`-Version, die bei fehlenden Skripten abbrach), werden durch den
aktuellen robusten Stub aus `SYSTEM_STUBS` ersetzt: fehlende
`run.sh`/`test.sh`/`.test_private.sh` werden übersprungen, fehlender
`.solution/`-Ordner → Ausgabe „Keine Lösung spezifiziert“ (Exit 0).
INDIVIDUELL angepasste Skripte (erkannt: kein Standard-Marker) bleiben
unangetastet und werden nur gemeldet.

Idempotent: beides ist sicher mehrmals ausführbar.

Aufruf (im AICampus-Container, damit Code + DB zusammenpassen):
    sudo docker compose -f deploy/compose.local.yml exec aicampus \\
        timeout 120 python -m scripts.migrate_rename_test_solution --dry-run
    sudo docker compose -f deploy/compose.local.yml exec aicampus \\
        timeout 120 python -m scripts.migrate_rename_test_solution

Nebenwirkung (einmalig): die Access-Map ist Teil des init-Hashes →
Tasks mit init.sh bekommen nach dem Rename ein neues Task-Image gebaut
(funktionell identisch, init.sh sieht die 👤-Datei nicht).
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from database.base import create_db_and_tables, engine, migrate_schema  # noqa: E402
from database.models import Task, TaskType  # noqa: E402
from services.workspace_service import (  # noqa: E402
    SYSTEM_STUBS,
    file_disk_path,
    workspace_service,
)
from sqlmodel import Session, select  # noqa: E402

OLD = ".run_solution.sh"
NEW = ".test_solution.sh"


def _is_legacy_stub(content: str) -> bool:
    """True, wenn der Inhalt eine ältere STANDARD-Variante des
    Testlauf-Skripts ist (→ durch den aktuellen Stub ersetzbar).
    Individuelle Skripte (ohne Standard-Marker) liefern False."""
    c = content.strip()
    if "Keine Lösung spezifiziert" in c:
        return False  # aktuelle Version
    if "cp -rf .solution/. ./" not in c:
        return False  # kein Standard-Skript (z. B. versehentlich HTML)
    # ältere Varianten: `set -e`-Versionen ODER vorheriger robuster
    # Stub (erkennbar am „Musterlösung einbetten“-Header)
    return "set -e" in c or "Musterlösung einbetten" in c


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nur anzeigen, was migriert würde — nichts ändern")
    args = parser.parse_args()

    create_db_and_tables()
    migrate_schema()

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(Task.task_type == TaskType.WORKSPACE)
        ).all()
        if not tasks:
            print("Keine Workspace-Tasks gefunden.")
            return 0

        renamed = 0
        updated = 0
        for task in tasks:
            files = {f.path: f for f in workspace_service.task_files(session, task)}
            old_p = file_disk_path(task.id, OLD)
            new_p = file_disk_path(task.id, NEW)

            # ── Teil 1: Rename (idempotent) ────────────────────────
            if OLD in files:
                if NEW in files:
                    print(f"Task {task.id} ({task.title!r}): ABGEBROCHEN — "
                          f"{OLD} UND {NEW} existieren, bitte manuell "
                          f"auflösen.")
                    continue
                note = "" if old_p.is_file() else " (nur DB-Zeile, keine Disk-Datei)"
                print(f"Task {task.id} ({task.title!r}): {OLD} → {NEW}{note}")
                if args.dry_run:
                    print("  [dry-run] nichts geschrieben")
                else:
                    if old_p.is_file():
                        new_p.parent.mkdir(parents=True, exist_ok=True)
                        old_p.rename(new_p)
                    files[OLD].path = NEW
                    session.add(files[OLD])
                    session.commit()
                    renamed += 1

            # ── Teil 2: Stub-Content → aktuelle robuste Version ────
            if NEW in files and new_p.is_file():
                if _is_legacy_stub(new_p.read_text(encoding="utf-8")):
                    print(f"Task {task.id} ({task.title!r}): {NEW} → aktueller "
                          f"robuster Stub")
                    if not args.dry_run:
                        new_p.write_text(SYSTEM_STUBS[NEW], encoding="utf-8")
                        updated += 1
                else:
                    print(f"Task {task.id} ({task.title!r}): {NEW} mit "
                          f"individuellerem Inhalt → unverändert (manuell "
                          f"prüfen!)")
            elif NEW not in files and OLD not in files:
                print(f"Task {task.id} ({task.title!r}): übersprungen "
                      f"(kein {NEW})")

        if args.dry_run:
            print("\n[dry-run] keine Änderungen vorgenommen.")
        else:
            print(f"\nFertig: {renamed} Task(s) umbenannt, "
                  f"{updated} Stub(s) aktualisiert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
