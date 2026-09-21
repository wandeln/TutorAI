#!/usr/bin/env python3
"""
One-Shot-Migration: Workspace-YAML-Spec → skriptbasiertes Modell.

Für alle Tasks mit task_type=workspace und nicht leerem `workspace_spec`:
  - `run`                          → Datei run.sh
  - `test`                         → Datei tests/test_public.sh
  - `verify.command` bzw. `verify` (String)
                                    → Datei .solution/test_private.sh
  - `packages` und/oder Dataset-URL → Datei init.sh
  - `timeout`/`limits`/`internet`/`artifacts`/`main_file`
                                    → neue Simple-Task-Felder
  - workspace_spec + workspace_dataset → NULL

Ein manuell angepasstes `.solution/verify.sh` wird stattdessen in
`.solution/test_private.sh` UMBENANNNT (der Spec-Verify-Befehl wird dann
ignoriert — die manuelle Datei ist aktueller).

Idempotent: Tasks, die bereits eine run.sh haben, werden übersprungen
(es sei denn --force).

Aufruf (im TutorAI-Container, damit Code + DB zusammenpassen):
    sudo docker compose -f deploy/compose.local.yml exec tutorai \\
        timeout 120 python -m scripts.migrate_workspace_scripts --dry-run
    sudo docker compose -f deploy/compose.local.yml exec tutorai \\
        timeout 120 python -m scripts.migrate_workspace_scripts

Nach erfolgreichem Lauf: die Legacy-Spalten in database/base.py in
`column_drops` vermerken und den Server neu starten.
"""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import yaml  # noqa: E402

from database.base import create_db_and_tables, engine, migrate_schema  # noqa: E402
from database.models import Task, TaskType  # noqa: E402
from services.workspace_service import (  # noqa: E402
    file_disk_path,
    workspace_service,
)
from sqlmodel import Session, select  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _sh_script(command: str) -> bytes:
    """Skript aus einem einfachen Shell-Command (working_dir=/workspace)."""
    return f"#!/bin/sh\nset -e\ncd /workspace\n{command}\n".encode("utf-8")


def _verify_command(spec: dict) -> str:
    v = spec.get("verify")
    if isinstance(v, dict):
        return str(v.get("command") or "").strip()
    if isinstance(v, str):
        return v.strip()
    return ""


_MEM_RE = re.compile(r"^\d+(\.\d+)?[bkmg]$", re.IGNORECASE)


def _norm_memory(value) -> str:
    """Memory-Limit normalisieren (Einheit Pflicht, klein)."""
    v = str(value or "4g").strip().lower()
    if not _MEM_RE.match(v):
        digits = re.match(r"^(\d+(?:\.\d+)?)", v)
        v = (digits.group(1) if digits else "4") + "g"
    return v



def _dataset_dict(ds_raw, spec: dict) -> Optional[dict]:
    raw = ds_raw or spec.get("dataset")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return raw if isinstance(raw, dict) else None


def _init_sh(spec: dict, ds: Optional[dict]) -> Optional[bytes]:
    """init.sh aus `packages` und/oder Dataset-URL — None, wenn beides leer."""
    body: list[str] = []
    pkgs = spec.get("packages") or []
    if isinstance(pkgs, str):
        pkgs = [p.strip() for p in pkgs.split(",")]
    if pkgs:
        body.append("pip install --no-cache-dir "
                    + " ".join(shlex.quote(str(p)) for p in pkgs))
    if ds and ds.get("source") == "url" and ds.get("url"):
        rel = str(ds.get("path") or ds.get("name") or "dataset").lstrip("/")
        if rel.startswith("data/"):
            rel = rel[len("data/"):]
        url = str(ds["url"]).replace('"', "'")
        body.append(
            f'[ -f "/assets/data/{rel}" ] || curl -fL --retry 3 -o '
            f'"/assets/data/{rel}" "{url}"')
    if not body:
        return None
    return ("#!/bin/sh\n"
            "# Von der Migration aus der alten Workspace-Spec erzeugt "
            "(packages/dataset).\n"
            "set -e\n"
            + "\n".join(body) + "\n").encode("utf-8")


def _plan_task(session: Session, task: Task, spec: dict,
               ds_raw) -> tuple[list[tuple[str, bytes]], dict, Optional[str]]:
    """(Dateien, Feld-Overrides, ggf. zu löschende verify.sh) planen."""
    existing = {f.path for f in workspace_service.task_files(session, task)}
    if not isinstance(spec, dict):
        raise ValueError(f"Workspace-Spec ist kein Mapping: {spec!r}")
    ds = _dataset_dict(ds_raw, spec)

    files: list[tuple[str, bytes]] = []
    run_cmd = str(spec.get("run") or "").strip()
    if run_cmd:
        files.append(("run.sh", _sh_script(run_cmd)))
    test_cmd = str(spec.get("test") or "").strip()
    if test_cmd:
        files.append(("tests/test_public.sh", _sh_script(test_cmd)))

    delete_verify_sh: Optional[str] = None
    if ".solution/test_private.sh" not in existing:
        verify_sh = file_disk_path(task.id, ".solution/verify.sh")
        if verify_sh.is_file():
            # manuell angepasstes verify.sh → umbenennen (geht vor Spec-Befehl)
            files.append((".solution/test_private.sh", verify_sh.read_bytes()))
            delete_verify_sh = ".solution/verify.sh"
        else:
            vcmd = _verify_command(spec)
            if vcmd:
                files.append((".solution/test_private.sh", _sh_script(vcmd)))

    init = _init_sh(spec, ds)
    if init is not None:
        files.append(("init.sh", init))

    limits = spec.get("limits") or {}
    if not isinstance(limits, dict):
        limits = {}
    fields = {
        "workspace_timeout": int(spec.get("timeout") or 900),
        "workspace_cpu": float(limits.get("cpu") or 2.0),
        "workspace_memory": _norm_memory(limits.get("memory")),
        "workspace_internet": bool(spec.get("internet")),
    }
    main_file = spec.get("main_file")
    if main_file:
        fields["workspace_main_file"] = str(main_file)
    return files, fields, delete_verify_sh


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nur anzeigen, was migriert würde — nichts ändern")
    parser.add_argument("--force", action="store_true",
                        help="auch Tasks migrieren, die bereits eine run.sh haben")
    args = parser.parse_args()

    create_db_and_tables()
    migrate_schema()

    with Session(engine) as session:
        # Legacy-Spalten existieren noch in der DB, aber NICHT mehr im
        # Task-Modell (bewusst entfernt) → nur per rohem SQL lesbar.
        legacy = {
            r[0]: (r[1], r[2])
            for r in session.exec(text(
                "select id, workspace_spec, workspace_dataset from tasks "
                "where task_type='WORKSPACE'")).all()
        }
        tasks = session.exec(
            select(Task).where(Task.task_type == TaskType.WORKSPACE)
        ).all()
        if not tasks:
            print("Keine Workspace-Tasks gefunden.")
            return 0

        migrated = 0
        for task in tasks:
            old_spec_raw, old_ds_raw = legacy.get(task.id, (None, None))
            if not (old_spec_raw or "").strip():
                print(f"Task {task.id} ({task.title!r}): übersprungen (keine Spec)")
                continue
            existing = {f.path for f in workspace_service.task_files(session, task)}
            if "run.sh" in existing and not args.force:
                print(f"Task {task.id} ({task.title!r}): übersprungen "
                      "(hat bereits run.sh — --force für Override)")
                continue
            try:
                spec = yaml.safe_load(old_spec_raw) or {}
                files, fields, delete_verify_sh = _plan_task(
                    session, task, spec, old_ds_raw)
            except Exception as e:  # noqa: BLE001
                print(f"Task {task.id} ({task.title!r}): FEHLER: {e}")
                continue

            print(f"Task {task.id} ({task.title!r}):")
            for path, data in files:
                print(f"  + {path}  ({len(data)} Bytes)")
            for k, v in fields.items():
                print(f"  {k} = {v!r}")
            if delete_verify_sh:
                print(f"  - {delete_verify_sh}  (→ .solution/test_private.sh)")
            if args.dry_run:
                print("  [dry-run] nichts geschrieben")
                continue

            for path, data in files:
                workspace_service.save_task_file(session, task, path, data)
            if delete_verify_sh:
                workspace_service.delete_task_file(session, task, delete_verify_sh)
            for k, v in fields.items():
                setattr(task, k, v)
            session.add(task)
            session.commit()
            # Legacy-Spalten leeren (rohes SQL — Attribut existiert nicht mehr):
            session.exec(text(
                "update tasks set workspace_spec=NULL, workspace_dataset=NULL "
                "where id=:id"), params={"id": task.id})
            session.commit()
            migrated += 1

        if args.dry_run:
            print("\n[dry-run] keine Änderungen vorgenommen.")
        elif migrated:
            print(f"\nFertig: {migrated} Task(s) migriert.")
            print("Nächste Schritte je Task in der Tutor-UI: 🔄 Sync ausführen")
            print("und — falls init.sh entstanden ist — Init-Build starten.")
        else:
            print("\nNichts migriert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
