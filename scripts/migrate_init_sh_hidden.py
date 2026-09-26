#!/usr/bin/env python3
"""
One-Shot-Migration: ``init.sh`` → ``.init.sh`` (Rename + Access-Klasse
readonly → hidden).

``init.sh`` wird für Studenten versteckt (👤): der Student-Container
zeigt es nicht mehr, der Agent holt die Skript-Quelle per Init-Request
(b64) und persistiert sie in der privaten Region (``.private/.init.sh``)
— der Asset-Sync überträgt sie nicht mehr (hidden-Filter).

Für alle Workspace-Tasks mit einer ``init.sh``-DB-Zeile werden
Disk-Datei und DB-Zeile (TaskWorkspaceFile.path) umbenannt und die
explizite Access-Klasse auf ``hidden`` gesetzt (die DB-Update läuft
raw, NICHT via move_task_file/set_file_access — die System-Skript-
Guards blockieren beides). Orphan-Fall (Disk-Datei ohne DB-Zeile):
``.init.sh`` wird via save_task_file neu angelegt (access=hidden wird
per system_file_access ohnehin erzwungen).

Idempotent: ohne ``init.sh``-Zeile/Datei passiert nichts. Konflikt
(``init.sh`` UND ``.init.sh`` existieren) → Task wird übersprungen +
gemeldet (manuell auflösen).

NACH der Migration werden für alle betroffenen Tasks
``workspace_service.on_task_saved(session, task)`` aufgerufen (best
effort): Asset-Sync (Purge entfernt das alte ``init.sh`` vom Agenten)
+ Init-Build mit neuem Hash (Pfad-Rename + Access-Wechsel readonly→
hidden ändern die Access-Map → Hash wechselt garantiert).

AUFRUF: erst NACH dem Rebuild des compute-agent ausführen (der neue
Agent erwartet ``.init.sh`` in .private/ und purgt das alte
``init.sh`` nicht fälschlich):
    sudo -n docker compose -f deploy/compose.local.yml up -d --build \\
        compute-agent
    sudo -n docker compose -f deploy/compose.local.yml exec aicampus \\
        timeout 300 python -m scripts.migrate_init_sh_hidden --dry-run
    sudo -n docker compose -f deploy/compose.local.yml exec aicampus \\
        timeout 300 python -m scripts.migrate_init_sh_hidden
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
    file_disk_path,
    workspace_service,
)
from sqlmodel import Session, select  # noqa: E402

OLD = "init.sh"
NEW = ".init.sh"


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

        migrated: list[Task] = []
        for task in tasks:
            files = {f.path: f for f in workspace_service.task_files(session, task)}
            old_p = file_disk_path(task.id, OLD)
            new_p = file_disk_path(task.id, NEW)

            if NEW in files:
                if OLD in files or old_p.is_file():
                    print(f"Task {task.id} ({task.title!r}): ÜBERSPRUNGEN — "
                          f"{NEW} existiert schon UND {OLD} auch; bitte "
                          f"manuell auflösen.")
                else:
                    print(f"Task {task.id} ({task.title!r}): übersprungen "
                          f"(bereits {NEW})")
                continue

            if OLD not in files and not old_p.is_file():
                print(f"Task {task.id} ({task.title!r}): übersprungen "
                      f"(kein {OLD})")
                continue

            if OLD in files:
                row = files[OLD]
                note = "" if old_p.is_file() else \
                    " (nur DB-Zeile, keine Disk-Datei)"
                print(f"Task {task.id} ({task.title!r}): {OLD} → {NEW} "
                      f"(access → hidden){note}")
                if not args.dry_run:
                    if old_p.is_file():
                        old_p.rename(new_p)
                    row.path = NEW
                    row.access = "hidden"
                    session.add(row)
                    session.commit()
                migrated.append(task)
            else:
                # Orphan: Disk-Datei ohne DB-Zeile → neu anlegen
                print(f"Task {task.id} ({task.title!r}): {OLD} ohne DB-Zeile "
                      f"→ {NEW} anlegen (access → hidden)")
                if not args.dry_run:
                    data = old_p.read_bytes()
                    old_p.unlink(missing_ok=True)
                    workspace_service.save_task_file(
                        session, task, NEW, data, access="hidden")
                migrated.append(task)

        if args.dry_run:
            print("\n[dry-run] keine Änderungen vorgenommen "
                  "(on_task_saved würde NACH dem realen Lauf für "
                  f"{len(migrated)} Task(s) aufgerufen werden).")
            return 0

        # Asset-Sync (Purge entfernt das alte init.sh vom Agenten) +
        # Init-Build mit neuem Hash — best effort, Fehler nur melden.
        for task in migrated:
            try:
                result = workspace_service.on_task_saved(session, task)
                for a in result.get("assets", []):
                    print(
                        f"  Task {task.id} / {a.get('agent')}: "
                        f"assets={a.get('assets', {}).get('status', '?')} "
                        f"task_image={a.get('task_image', {}).get('status', '?')}")
            except Exception as e:  # noqa: BLE001 — nicht fatal
                print(f"  Task {task.id}: on_task_saved fehlgeschlagen "
                      f"(nächster Task-Save behebt es): {e}")

        print(f"\nFertig: {len(migrated)} Task(s) migriert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
