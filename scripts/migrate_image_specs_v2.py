"""Einmalige Migration: Image-Specs YAML → reines Dockerfile (2026-09-18).

1. ALTER TABLE image_specs RENAME COLUMN spec_yaml TO dockerfile
2. DROP COLUMN gpu_mode (nur SQLite ≥ 3.35; sonst bleibt die Spalte
   stehen — sie wird vom ORM ignoriert)
3. Die 4 globalen Seed-Specs (python-ml/cpp/asm/server) werden mit den
   neuen Dockerfile-Inhalten ÜBERSCHRIEBEN (sie referenzierten die
   kuratierten aicampus/*-Images, die es nicht mehr gibt)
4. Legacy-Tasks: leeres workspace_image + Preset in der Workspace-Spec
   → workspace_image auf den passenden Seed-Spec-Namen setzen (die
   Tasks laufen dann überall über das Spec-System)

Aufruf aus dem Projekt-Root:  python migrate_image_specs_v2.py
Im Container:                 python3 /app/migrate_image_specs_v2.py
"""

import yaml
from sqlalchemy import text
from sqlmodel import Session, select

from database.base import engine
from database.models import ImageSpec, Task
from services.image_spec_service import SEED_GLOBAL_SPECS

# Preset-Key (Workspace-Spec) → globaler Seed-Spec-Name
PRESET_TO_SPEC = {
    "python-ml": "python-ml",
    "cpp": "cpp",
    "cpp-mk": "cpp",
    "asm": "asm",
    "server": "server",
}


def migrate_schema() -> None:
    """1+2) Spalten umbenennen/entfernen (raw SQL, idempotent)."""
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql(
            "PRAGMA table_info(image_specs)").fetchall()}
        if not cols:
            print("Tabelle image_specs existiert nicht (frische DB) — nichts zu tun.")
            return
        if "spec_yaml" in cols:
            conn.exec_driver_sql(
                "ALTER TABLE image_specs RENAME COLUMN spec_yaml TO dockerfile")
            print("Umbenannt: image_specs.spec_yaml → dockerfile")
        elif "dockerfile" in cols:
            print("Spalte dockerfile bereits vorhanden (Migration läuft? ok)")
        if "gpu_mode" in cols:
            ver = conn.exec_driver_sql("SELECT sqlite_version()").scalar()
            try:
                major, minor = (int(x) for x in ver.split(".")[:2])
            except ValueError:
                major, minor = 0, 0
            if (major, minor) >= (3, 35):
                conn.exec_driver_sql("ALTER TABLE image_specs DROP COLUMN gpu_mode")
                print(f"Entfernt: image_specs.gpu_mode (SQLite {ver})")
            else:
                print(f"SQLite {ver} < 3.35 — gpu_mode-Spalte bleibt stehen "
                      "(wird vom ORM ignoriert)")


def migrate_data() -> None:
    """3+4) Seed-Specs überschreiben + Legacy-Tasks migrieren."""
    with Session(engine) as s:
        for name, dockerfile in SEED_GLOBAL_SPECS:
            row = s.exec(select(ImageSpec).where(
                ImageSpec.scope == "global", ImageSpec.name == name
            )).first()
            if row is None:
                s.add(ImageSpec(scope="global", name=name,
                                dockerfile=dockerfile, created_by=None))
                print(f"Seed-Spec {name!r} neu angelegt")
            elif row.dockerfile != dockerfile:
                row.dockerfile = dockerfile
                s.add(row)
                print(f"Seed-Spec {name!r} mit neuem Dockerfile überschrieben")

        cnt = 0
        for task in s.exec(select(Task)).all():
            if task.workspace_image or not task.workspace_spec:
                continue
            try:
                sp = yaml.safe_load(task.workspace_spec) or {}
            except yaml.YAMLError:
                continue
            preset = sp.get("preset") if isinstance(sp, dict) else None
            target = PRESET_TO_SPEC.get(str(preset or ""))
            if not target:
                continue
            task.workspace_image = target
            s.add(task)
            cnt += 1
            print(f"Task {task.id} ({task.title!r}): workspace_image → {target!r}")
        s.commit()
        print(f"Fertig: 4 Seed-Specs + {cnt} Legacy-Task(s) migriert.")


if __name__ == "__main__":
    migrate_schema()
    migrate_data()
