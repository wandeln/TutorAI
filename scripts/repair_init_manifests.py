#!/usr/bin/env python3
"""Einmal-Reparatur der Init-Manifests (data/compute-assets/{course}/{task}/).

Hintergrund: Das Manifest listete früher nur die Schreib-Ergebnisse des
jeweiligen Einzel-Builds (after−before-Diff). Dateien, die auf Disk schon
vorhanden waren (z. B. aus einem früheren Build mit anderer Access-Config),
fielen damit aus dem Manifest und wurden im Tutor-Baum unsichtbar
([init]-Einträge).

Reparatur (spiegelt die neue Build-Semantik aus docker_ops._do_init_build):
- private = Voll-Snapshot von .private/ (ohne init_private.sh)
- shared  = alte Manifest-Einträge, die noch auf Disk existieren
- seed    = alte Manifest-Einträge, die noch in .seeds/ existieren;
            .seeds/-Dateien OHNE Manifest-Eintrag werden gelöscht

Nur Tasks mit vorhandenem Manifest sind betroffen. Idempot.

Aufruf (root-Berechtigung für data/compute-assets):
    sudo python3 scripts/repair_init_manifests.py [--dry-run]
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "data" / "compute-assets"
MANIFEST = ".init_manifest.json"


def file_set(d: Path) -> set[str]:
    if not d.is_dir():
        return set()
    return {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Init-Manifests reparieren (siehe Module-Docstring).")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur anzeigen, nichts ändern")
    args = ap.parse_args()

    if not ASSETS.is_dir():
        print(f"Kein Assets-Verzeichnis: {ASSETS}")
        return

    for m in sorted(ASSETS.glob("*/*/" + MANIFEST)):
        base = m.parent
        data = json.loads(m.read_text(encoding="utf-8"))
        old_shared = {x for x in (data.get("shared") or [])}
        old_seed = {x for x in (data.get("seed") or [])}
        priv, seeds = base / ".private", base / ".seeds"

        new_private = sorted(file_set(priv) - {"init_private.sh"})
        new_shared = sorted(p for p in old_shared if (base / p).is_file())
        new_seed = sorted(p for p in old_seed if (seeds / p).is_file())
        stale_seed_files = sorted(file_set(seeds) - set(new_seed))

        new_data = {"shared": new_shared, "seed": new_seed,
                    "private": new_private}
        if new_data == data and not stale_seed_files:
            print(f"{base.relative_to(ROOT)}: OK")
            continue

        print(f"{base.relative_to(ROOT)}: reparieren")
        for k in ("shared", "seed", "private"):
            if sorted(data.get(k) or []) != new_data[k]:
                print(f"  {k}: {sorted(data.get(k) or [])} → {new_data[k]}")
        if stale_seed_files:
            print(f"  .seeds: alte Dateien löschen {stale_seed_files}")
        if args.dry_run:
            continue
        m.write_text(
            json.dumps(new_data, indent=1, ensure_ascii=False),
            encoding="utf-8")
        for rel in stale_seed_files:
            (seeds / rel).unlink(missing_ok=True)

    if args.dry_run:
        print("(dry-run: nichts geändert)")


if __name__ == "__main__":
    main()
