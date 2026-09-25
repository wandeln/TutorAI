"""
Workspace-Spec (JSON-dict) — Parsing & Validierung.

Seit der Umstellung auf das Skript-Modell ist die Spec bewusst klein
(„Slim-Spec“): Run-/Test-/Verify-Befehle, Pakete, Environment-Vars und
zusätzliche Mounts gibt es nicht mehr — das übernehmen die Task-Skripte
(`run.sh`, `test.sh`, `.init.sh`), die als Dateien in der
Aufgabe liegen (🔒-Skripte read-only gemountet, 👤-Skripte nie im
Student-Container).

TutorAI injiziert das konkrete Container-IMAGE (aus der Image-Spec der
Aufgabe) in `image` sowie — wenn die Aufgabe Init-Skripte hat — die
Task-Image-Referenz in `task_image` (vom Agent gebaut via init-build).
Die GPU-Fähigkeit folgt aus der Compute-Engine: TutorAI injiziert die
Engine-Regel in `gpus` ("all" | "none" | [int, …]).

`readonly_paths`: Top-Level-Pfade der Zugriffs-Klasse 🔒 (Dateien oder
Ordner) — jeder wird aus dem geteilten Asset-Verzeichnis read-only nach
/workspace/<pfad> gemountet (eine Kopie für alle Studenten der Aufgabe).
"""

import re

from . import config


class SpecError(Exception):
    """Ungültige Workspace-Spec (Meldung ist UI-tauglich)."""


ALLOWED_KEYS = {
    "image", "task_image", "limits", "timeout", "gpus",
    "internet", "main_file", "readonly_paths", "disk_quota_mb",
}

_MEM_RE = re.compile(r"^\d+(\.\d+)?[bkmg]?$", re.IGNORECASE)


def _str(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{field} muss ein (nicht leerer) String sein")
    return value.strip()


def _bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
        return value.strip().lower() in ("true", "1")
    raise SpecError(f"{field} muss true/false sein")


def _int(value, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"{field} muss eine ganze Zahl sein")
    v = int(value)
    if not (lo <= v <= hi):
        raise SpecError(f"{field} muss zwischen {lo} und {hi} liegen")
    return v


def _gpus(value, field: str = "gpus") -> str | list[int] | None:
    """GPU-Modus der Engine: "all" | "none" | Liste von GPU-Nummern.

    None = nicht gesetzt (TutorAI injiziert die Engine-Regel; Default ohne
    Injektion ist "none" — kein GPU).
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("all", "none"):
            return s
        raise SpecError(f"{field}: String muss \u201eall\u201c oder \u201enone\u201c sein")
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 128):
                raise SpecError(f"{field}: Einträge müssen GPU-Nummern (0..128) sein")
            out.append(v)
        return sorted(set(out))
    raise SpecError(f"{field}: muss \u201eall\u201c, \u201enone\u201c oder eine GPU-Nummern-Liste sein")


def _readonly_paths(value) -> list[str]:
    """Read-only-Zugriffsklasse-Pfade (🔒, top-level): Liste relativer
    Pfade (Dateien ODER Ordner) — je Pfad ein ro-Bind-Mount aus dem
    Asset-Verzeichnis nach /workspace/<pfad>."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise SpecError("readonly_paths muss eine String-Liste sein")
    out = []
    for v in value:
        if not isinstance(v, str):
            raise SpecError("readonly_paths: Einträge müssen Strings sein")
        p = v.strip().replace("\\", "/").lstrip("/")
        if not p or p == "." or any(x in ("", ".", "..") for x in p.split("/")):
            raise SpecError(f"readonly_paths: ungültiger Pfad {v!r}")
        if p not in out:
            out.append(p)
    return sorted(out)


def _limits(value, field: str = "limits") -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SpecError(f"{field} muss ein Mapping {{cpu, memory}} sein")
    out = {}
    cpu = value.get("cpu")
    if cpu is not None:
        if isinstance(cpu, bool) or not isinstance(cpu, (int, float)) or not (0 < float(cpu) <= 32):
            raise SpecError(f"{field}.cpu muss eine Zahl > 0 (max. 32) sein")
        out["cpu"] = float(cpu)
    mem = value.get("memory")
    if mem is not None:
        if isinstance(mem, (int, float)) and not isinstance(mem, bool):
            mem = str(mem)
        mem = _str(mem, f"{field}.memory").lower().replace(" ", "")
        if not _MEM_RE.match(mem):
            raise SpecError(f"{field}.memory: Format z.B. '512m', '4g', '2g'")
        out["memory"] = mem
    return out


def parse_spec(spec) -> dict:
    """Slim-Spec (dict) validieren & normalisieren.

    Liefert ein dict mit garantiert vorhandenen, sauberen Keys:
    image, task_image, working_dir, limits, timeout, gpus, internet,
    main_file, readonly_paths, disk_quota_mb. (`image` ist Pflicht,
    `task_image`, `main_file` und `disk_quota_mb` optional — das Image
    wird von TutorAI aus der Image-Spec der Aufgabe injiziert.)
    """
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise SpecError(
            "Spec muss ein JSON-Object sein (die alte YAML-Spec wird "
            "nicht mehr unterstützt)")

    # Entfernt mit der Subdomain-Preview (Apps laufen auf /) — alte
    # Instanzen können den Key noch kurz nach dem Update senden:
    # still ignorieren statt alle Workspaces hart zu brechen.
    spec = {k: v for k, v in spec.items() if k != "preview_root_ports"}

    unknown = set(spec) - ALLOWED_KEYS
    if unknown:
        raise SpecError(f"Unbekannte Key(s): {', '.join(sorted(unknown))}")

    image = spec.get("image")
    image = _str(image, "image") if image is not None else None
    if image is None:
        raise SpecError(
            "image fehlt (wird normalerweise von TutorAI aus der "
            "Image-Spec der Aufgabe injiziert)")
    if image.startswith("/") or ".." in image:
        raise SpecError("image: Host-Pfade sind nicht erlaubt")

    task_image = spec.get("task_image")
    task_image = _str(task_image, "task_image") if task_image is not None else None
    if task_image is not None and (task_image.startswith("/") or ".." in task_image):
        raise SpecError("task_image: Host-Pfade sind nicht erlaubt")

    limits = _limits(spec.get("limits"))
    timeout = _int(spec.get("timeout", config.DEFAULT_TIMEOUT),
                   "timeout", 1, config.MAX_TIMEOUT)

    # GPU-Regel der Engine ("gpus", injiziert von TutorAI). Ohne Injektion:
    # kein GPU — die Fähigkeit folgt immer aus der Compute-Engine.
    gpus = _gpus(spec.get("gpus"))
    if gpus is None:
        gpus = "none"

    # main_file: Editor-Einstiegspunkt (optional; relativ zum Workspace)
    main_file = spec.get("main_file")
    main_file = _str(main_file, "main_file") if main_file is not None else None
    if main_file is not None:
        if main_file.startswith("/") or ".." in main_file.split("/"):
            raise SpecError("main_file muss ein relativer Pfad sein (kein ..)")

    readonly_paths = _readonly_paths(spec.get("readonly_paths"))

    # Disk-Quota des Student-Volumes in MB (0 = ohne Limit → None).
    disk_quota_mb = None
    if spec.get("disk_quota_mb") is not None:
        disk_quota_mb = _int(spec["disk_quota_mb"], "disk_quota_mb",
                             0, 100 * 1024)
        if disk_quota_mb == 0:
            disk_quota_mb = None

    return {
        "image": image,
        "task_image": task_image,
        "working_dir": "/workspace",
        "limits": limits,
        "timeout": timeout,
        "gpus": gpus,
        "internet": _bool(spec.get("internet", False), "internet"),
        "main_file": main_file,
        "readonly_paths": readonly_paths,
        "disk_quota_mb": disk_quota_mb,
    }
