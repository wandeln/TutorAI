"""
Image-Spec-Service: Storage & Auflösung der Image-"Rezepte"
(s. docs/plan-compute-engines-images.md).

Format seit 2026-09-18: Die Spec ist ein REINES DOCKERFILE (DB-Spalte
`dockerfile`), der Name (Slug) liegt als eigene DB-Spalte daneben.
Kein YAML-Wrapper, keine variants, kein gpu_mode (GPU-Regeln gehören
zur Compute-Engine).

- Scope: "global" (Admin) oder Kurs (Prof); (scope, name) ist eindeutig.
- Validierung/Hashing liegen im geteilten Modul compute_agent.image_spec
  (dieselbe Logik wie im Agent — eine Quelle für beide Seiten).
- resolve_task_image: Task → konkrete Image-Referenz, die in die
  Workspace-Spec injiziert wird (No-op-Spec → Basis-Image direkt).
"""

import json
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from compute_agent.image_spec import (
    ImageSpecError as _AgentImageSpecError,
    base_image as _df_base_image,
    image_tags as _df_image_tags,
    name_ok as _name_ok,
    validate as _df_validate,
)
from database.models import ImageSpec, Task


class ImageSpecError(Exception):
    """Ungültige/fehlende Image-Spec (Meldung ist UI-tauglich)."""


# Kuratierte globale Seed-Specs: Beispiele für typische Kurs-Umgebungen,
# alle von öffentlichen Base-Images abgeleitet (die komplette Umgebung
# steht transparent im Dockerfile).
SEED_GLOBAL_SPECS: list[tuple[str, str]] = [
    ("python-ml",
     "FROM python:3.11-slim\n"
     "\n"
     "ENV PYTHONUNBUFFERED=1 \\\n"
     "    PIP_NO_CACHE_DIR=1\n"
     "\n"
     "RUN pip install --no-cache-dir \\\n"
     "    numpy==1.26.4 \\\n"
     "    pandas==2.2.2 \\\n"
     "    scikit-learn==1.5.1 \\\n"
     "    matplotlib==3.9.2 \\\n"
     "    requests==2.32.3 \\\n"
     "    torch==2.4.1 \\\n"
     "    torchvision==0.19.1 \\\n"
     "    pytest==8.3.1\n"
     "\n"
     "WORKDIR /workspace\n"
     "\n"
     'CMD ["python"]\n'),
    ("cpp",
     "FROM debian:bookworm-slim\n"
     "\n"
     "ENV DEBIAN_FRONTEND=noninteractive \\\n"
     "    TZ=UTC\n"
     "\n"
     "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
     "        build-essential \\\n"
     "        gcc g++ \\\n"
     "        clang lld \\\n"
     "        make cmake ninja-build \\\n"
     "        gdb \\\n"
     "        pkg-config \\\n"
     "        # GL/X11-Header für Grafik-Aufgaben (GLFW/SDL2/OpenGL)\n"
     "        libgl1-mesa-dev libglu1-mesa-dev \\\n"
     "        libx11-dev libxext-dev libxrandr-dev libxinerama-dev \\\n"
     "        libwayland-dev libegl-dev \\\n"
     "        libglfw3-dev libsdl2-dev \\\n"
     "        libasound2-dev \\\n"
     "        # Headless X für Fenster-Aufgaben: xvfb-run ./solution\n"
     "        xvfb \\\n"
     "    && rm -rf /var/lib/apt/lists/*\n"
     "\n"
     "WORKDIR /workspace\n"
     "\n"
     'CMD ["bash"]\n'),
    ("asm",
     "FROM debian:bookworm-slim\n"
     "\n"
     "ENV DEBIAN_FRONTEND=noninteractive \\\n"
     "    TZ=UTC\n"
     "\n"
     "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
     "        nasm \\\n"
     "        yasm \\\n"
     "        binutils \\\n"
     "        gcc libc6-dev \\\n"
     "        make \\\n"
     "        gdb \\\n"
     "    && rm -rf /var/lib/apt/lists/*\n"
     "\n"
     "WORKDIR /workspace\n"
     "\n"
     'CMD ["bash"]\n'),
    ("server",
     "FROM debian:bookworm-slim\n"
     "\n"
     "ENV DEBIAN_FRONTEND=noninteractive \\\n"
     "    TZ=UTC\n"
     "\n"
     "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
     "        nginx \\\n"
     "        openssh-server \\\n"
     "        bind9 bind9utils \\\n"
     "        ufw \\\n"
     "        curl ca-certificates \\\n"
     "        net-tools iproute2 procps \\\n"
     "        dnsutils iputils-ping \\\n"
     "        openssl \\\n"
     "    && rm -rf /var/lib/apt/lists/* \\\n"
     "    && mkdir -p /run/sshd \\\n"
     "    # Host-Keys vorgeneriert, damit `service ssh start` sofort läuft\n"
     "    && ssh-keygen -A\n"
     "\n"
     "WORKDIR /workspace\n"
     "\n"
     'CMD ["bash"]\n'),
]


def seed_global_specs(session: Session) -> int:
    """Kuratierte globale Seed-Specs anlegen (idempotent)."""
    n = 0
    for name, dockerfile in SEED_GLOBAL_SPECS:
        exists = session.exec(
            select(ImageSpec).where(
                ImageSpec.scope == "global", ImageSpec.name == name
            )
        ).first()
        if exists:
            continue
        session.add(ImageSpec(
            scope="global",
            name=name,
            dockerfile=dockerfile,
            created_by=None,
        ))
        n += 1
    if n:
        session.commit()
    return n


# ── Lesen ─────────────────────────────────────────────────────────


def spec_info(row: ImageSpec) -> dict:
    """UI-taugliches Info-Dict (Base/Tags aus dem Dockerfile abgeleitet)."""
    try:
        _df_validate(row.dockerfile)
        base = _df_base_image(row.dockerfile)
        tags = _df_image_tags(row.name, row.dockerfile)
    except _AgentImageSpecError:
        base, tags = "", []
    return {
        "id": row.id,
        "scope": row.scope,
        "name": row.name,
        "base": base,
        "dockerfile": row.dockerfile,
        "tags": tags,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_specs(session: Session, course_id: Optional[int] = None) -> list[dict]:
    """Globale Specs + (falls Kurs) Kurs-Specs — Kurs zuerst, dann global."""
    scopes = ["global"]
    if course_id is not None:
        scopes.insert(0, str(course_id))
    rows = session.exec(
        select(ImageSpec).where(ImageSpec.scope.in_(scopes))  # type: ignore[attr-defined]
    ).all()
    rows = sorted(rows, key=lambda r: (0 if r.scope != "global" else 1, r.name))
    return [spec_info(r) for r in rows]


def resolve_spec(session: Session, course_id: Optional[int],
                 name: str) -> Optional[ImageSpec]:
    """Kurs-Scope schlägt global (gleicher Name → Kurs-Spec gewinnt)."""
    if course_id is not None:
        row = session.exec(
            select(ImageSpec).where(
                ImageSpec.scope == str(course_id), ImageSpec.name == name
            )
        ).first()
        if row is not None:
            return row
    return session.exec(
        select(ImageSpec).where(
            ImageSpec.scope == "global", ImageSpec.name == name
        )
    ).first()


def get_by_id(session: Session, spec_id: int) -> Optional[ImageSpec]:
    return session.get(ImageSpec, spec_id)


# ── Schreiben (Admin global / Prof Kurs) ─────────────────────────


def save_spec(session: Session, scope: str, name: str, dockerfile: str,
              created_by: Optional[int] = None) -> ImageSpec:
    """Spec upserten (validiert; Name und Dockerfile werden geprüft)."""
    name = str(name or "").strip()
    dockerfile = str(dockerfile or "").strip()
    if not _name_ok(name):
        raise ImageSpecError(
            f"Name {name!r} ungültig (erlaubt: a-z, 0-9, -, _; "
            "beginnt mit Buchstabe/Zahl)")
    try:
        _df_validate(dockerfile)
    except _AgentImageSpecError as e:
        raise ImageSpecError(str(e)) from e
    row = session.exec(
        select(ImageSpec).where(
            ImageSpec.scope == scope, ImageSpec.name == name
        )
    ).first()
    if row is None:
        row = ImageSpec(scope=scope, name=name)
    row.dockerfile = dockerfile
    row.created_by = created_by or row.created_by
    row.updated_at = datetime.now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_spec(session: Session, spec_id: int) -> None:
    row = get_by_id(session, spec_id)
    if row is None:
        return
    for task in session.exec(
        select(Task).where(Task.workspace_image == row.name)
    ).all():
        if row.scope == "global" or row.scope == str(task.course_id):
            raise ImageSpecError(
                f"Spec {row.name!r} wird von Aufgabe {task.title!r} verwendet")
    session.delete(row)
    session.commit()


# ── Task-Auflösung (Routing/Image-Ref) ───────────────────────────


def task_engine_names(task: Task) -> Optional[list[str]]:
    """Geordneter Engine-Pool aus task.workspace_engines (JSON).
    None = kein Pool → alle registrierten Agents."""
    if not task.workspace_engines:
        return None
    try:
        names = json.loads(task.workspace_engines)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(names, list):
        return None
    names = [str(n).strip() for n in names if str(n).strip()]
    return names or None


def resolve_task_image(session: Session, task: Task) -> Optional[dict]:
    """Task → konkrete Image-Referenz der Image-Spec.

    Liefert {"image", "spec_name"} oder None (Legacy-Task ohne
    workspace_image → Workspace-Spec/Preset entscheidet).
    """
    if not task.workspace_image:
        return None
    row = resolve_spec(session, task.course_id, task.workspace_image)
    if row is None:
        return None
    try:
        _df_validate(row.dockerfile)
    except _AgentImageSpecError as e:
        raise ImageSpecError(f"Image-Spec {row.name!r} ungültig: {e}") from e
    return {
        "image": _df_image_tags(row.name, row.dockerfile)[0],
        "spec_name": row.name,
    }
