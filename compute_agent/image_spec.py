"""
Image-Spec — reines Dockerfile als „Rezept" für ein Workspace-Image
(s. docs/plan-compute-engines-images.md, Format-Wechsel 2026-09-18).

Die Spec ist ein Dockerfile (DB-Spalte `dockerfile`), der Name (Slug)
liegt daneben als eigene DB-Spalte — keine YAML-Wrapper mehr, keine
variants, kein GPU-Modus (GPU-Regeln gehören zur Compute-Engine).

Wichtige Eigenschaften:
- Erste Instruktion MUSS ein FROM mit öffentlichem Base-Image sein.
- COPY/ADD verboten (der Build-Kontext ist ein leeres Temp-Verzeichnis).
- No-op-Specs (nur FROM + Kommentare) bauen nichts — das Basis-Image
  wird direkt referenziert.
- Deterministischer Inhalts-Hash über das NORMALISIERTE Dockerfile
  (Kommentare/Whitespace zählen nicht) → identische Tags auf allen
  Engines, keine Neutagging-Müll durch Kommentar-Änderungen.
"""

import hashlib
import re

__all__ = ["ImageSpecError", "name_ok", "validate", "base_image",
           "has_build_content", "normalize", "content_hash", "image_tags"]


class ImageSpecError(Exception):
    """Ungültige Image-Spec (Meldung ist UI-tauglich)."""


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
# Docker-Image-Referenz (Name, optionales :Tag, optionale @Digest)
_BASE_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,255}"
    r"(:[a-zA-Z0-9._-]{1,128})?"
    r"(@sha256:[a-f0-9]{64})?$")
_FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.IGNORECASE)
_MAX_LEN = 50_000
# Legacy: YAML-Spec mit diesen Top-Level-Keys (altes Format vor 2026-09-18)
_LEGACY_KEY_RE = re.compile(
    r"^\s*(name|base|description|packages|gpu|variants|extra_steps)\s*:",
    re.IGNORECASE)


def name_ok(name: str) -> bool:
    """Slug prüfen (a-z, 0-9, -, _; beginnt mit Buchstabe/Zahl)."""
    return bool(_NAME_RE.match(name or ""))


def _non_comment_lines(text: str) -> list[str]:
    """Nicht-leere, nicht-Kommentar-Zeilen (gestrippt)."""
    return [l.strip() for l in (text or "").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def validate(dockerfile: str) -> None:
    """Spec-Dockerfile validieren (UI-taugliche Fehler)."""
    text = (dockerfile or "").strip()
    if not text:
        raise ImageSpecError("Dockerfile ist leer.")
    if len(text) > _MAX_LEN:
        raise ImageSpecError(f"Dockerfile zu lang (max. {_MAX_LEN} Zeichen).")
    for l in text.splitlines():
        if _LEGACY_KEY_RE.match(l):
            raise ImageSpecError(
                "Sieht nach dem alten Image-Spec-Format aus (YAML mit "
                "name:/base:/…). Specs sind jetzt reine Dockerfiles — "
                "übertragen: Base → FROM, Pakete → RUN pip/apt install.")
    lines = _non_comment_lines(text)
    if not lines:
        raise ImageSpecError("Dockerfile enthält keine FROM-Instruktion.")
    m = _FROM_RE.match(lines[0])
    if not m:
        raise ImageSpecError(
            "Die erste Instruktion muss FROM sein (z. B. FROM python:3.11-slim).")
    base = m.group(1)
    if ".." in base or any(c.isspace() for c in base):
        raise ImageSpecError(f"Base-Image {base!r} ungültig.")
    if not _BASE_RE.match(base):
        raise ImageSpecError(
            f"Base-Image {base!r} ist keine gültige Docker-Image-Referenz.")
    for l in lines:
        if re.match(r"^(COPY|ADD)\b", l, re.IGNORECASE):
            raise ImageSpecError(
                "COPY/ADD ist nicht erlaubt (Build-Kontext ist leer) — "
                "Pakete direkt per RUN installieren.")


def base_image(dockerfile: str) -> str:
    """Base-Image aus der ersten FROM-Instruktion (leer = keine)."""
    for l in _non_comment_lines(dockerfile):
        m = _FROM_RE.match(l)
        if m:
            return m.group(1)
    return ""


def has_build_content(dockerfile: str) -> bool:
    """False = No-op-Spec (Basis-Image wird direkt referenziert)."""
    lines = _non_comment_lines(dockerfile)
    return any(not _FROM_RE.match(l) for l in lines)


def normalize(dockerfile: str) -> str:
    """Hash-Normalisierung: Kommentare/leere Zeilen raus, Zeilen trimmen.

    Verhindert Neutagging, wenn nur Kommentare oder Whitespace sich ändern.
    """
    return " ".join(l.strip() for l in (dockerfile or "").splitlines()
                    if l.strip() and not l.strip().startswith("#"))


def content_hash(dockerfile: str) -> str:
    """Stabiler Inhalts-Hash (SHA256[:12] über das normalisierte Dockerfile)."""
    return hashlib.sha256(normalize(dockerfile).encode()).hexdigest()[:12]


def image_tags(name: str, dockerfile: str) -> list[str]:
    """Konkrete Image-Referenz(en), die diese Spec auf einem Node belegt.

    - No-op (nur FROM + Kommentare): nur das Basis-Image selbst.
    - sonst: genau EIN deterministisches Tag (Name + Inhalts-Hash).
    """
    if not has_build_content(dockerfile):
        return [base_image(dockerfile)]
    return [f"tutorai/spec/{name}:{content_hash(dockerfile)}"]
