"""
Medienbibliothek: Datei-Speicherung + MediaUsage-Reconciliation.

Speicherort:  data/media/course_{id}/<uuid>.<ext>  (siehe config.MEDIA_DIR)
Versand:      authentifizierte Route GET /media/{course_id}/{filename} (main.py)
              — kein Static-Mount, damit Medien versteckter Aufgaben nicht
              ohne Kurs-Membership abrufbar sind.

Single Source of Truth für Einbindungen ist das Markdown selbst
(task.description / material.content, Referenz: /media/{course_id}/<datei>).
sync_media_usages() baut die MediaUsage-Tabelle daraus neu auf.
"""

import re
import uuid
from pathlib import Path
from typing import Optional, TypedDict

from fastapi import HTTPException
from sqlmodel import Session, select

from config import MEDIA_DIR
from database.models import (
    CourseMaterial,
    CourseMedia,
    MaterialType,
    MediaUsage,
    ScriptSection,
    Task,
)

# ─── Upload-Limits & Whitelist ────────────────────────────────────
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
ALLOWED_MEDIA: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# SVG wird bewusst nicht erlaubt (XSS-Risiko via embedded Script)

_FILENAME_RE = re.compile(r"[A-Za-z0-9._\-]+\Z")


class RefEntry(TypedDict):
    """Eine Quellen-Referenz auf ein Medium (pro Aufgabe/Material dedupliziert)."""
    task_id: Optional[int]
    material_id: Optional[int]
    location: str
    count: int


class RefInfo(TypedDict):
    """Aggregierte Referenz-Infos pro Datei für die Medien-Übersicht."""
    total: int
    duplicates: list[str]


def media_url(media: CourseMedia) -> str:
    """Öffentliche (authentifizierte) URL eines Mediums."""
    return f"/media/{media.course_id}/{media.file_path.rsplit('/', 1)[-1]}"


def media_dir_for_course(course_id: int) -> Path:
    d = MEDIA_DIR / f"course_{course_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(course_id: int, filename: str, data: bytes) -> tuple[str, str, int]:
    """Validiert + speichert einen Upload.

    Returns: (relativer file_path, mime_type, size)
    Raises:  400/413 bei ungültigem Upload.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_MEDIA:
        allowed = ", ".join(sorted(ALLOWED_MEDIA))
        raise HTTPException(400, f"Dateityp nicht erlaubt. Erlaubt: {allowed}")
    if not data:
        raise HTTPException(400, "Datei ist leer.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Datei zu groß (max. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    course_dir = media_dir_for_course(course_id)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    (course_dir / stored_name).write_bytes(data)
    return f"course_{course_id}/{stored_name}", ALLOWED_MEDIA[ext], len(data)


def replace_file(rel_path: str, filename: str, data: bytes) -> tuple[str, str, int]:
    """Überschreibt eine bestehende Medium-Datei am selben Pfad.

    Der Dateiname (UUID + Extension) bleibt unverändert, damit Markdown-Referenzen
    und gespeicherte URLs weiter funktionieren — nur Inhalt, MIME-Type und Größe
    werden aktualisiert.

    Returns: (relativer file_path, mime_type, size)
    Raises:  400/404/413 bei ungültigem Upload.
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_MEDIA:
        allowed = ", ".join(sorted(ALLOWED_MEDIA))
        raise HTTPException(400, f"Dateityp nicht erlaubt. Erlaubt: {allowed}")
    if not data:
        raise HTTPException(400, "Datei ist leer.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Datei zu groß (max. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    p = (MEDIA_DIR / rel_path).resolve()
    if not p.is_relative_to(MEDIA_DIR.resolve()) or not p.is_file():
        raise HTTPException(404, "Bestehende Datei nicht gefunden.")
    p.write_bytes(data)
    return rel_path, ALLOWED_MEDIA[ext], len(data)


def resolve_media_path(course_id: int, filename: str) -> Path | None:
    """Löst einen Dateinamen auf eine sichere Datei unter MEDIA_DIR/course_{id}.

    Returns: Pfad (wenn Datei existiert) oder None.
    """
    if not _FILENAME_RE.match(filename):
        return None
    p = (MEDIA_DIR / f"course_{course_id}" / filename).resolve()
    if not p.is_relative_to(MEDIA_DIR.resolve()):
        return None
    return p if p.is_file() else None


def delete_file(file_path: str) -> None:
    """Löscht die Datei auf der Festplatte (best effort)."""
    try:
        p = (MEDIA_DIR / file_path).resolve()
        if p.is_relative_to(MEDIA_DIR.resolve()) and p.is_file():
            p.unlink()
    except OSError:
        pass  # Datei fehlt schon oder FS-Problem — DB-Eintrag wird trotzdem gelöscht


# ─── Referenz-Scan + Reconciliation ───────────────────────────────

def _reference_pattern(course_id: int) -> re.Pattern[str]:
    return re.compile(rf"/media/{course_id}/([A-Za-z0-9._\-]+)")


def _scan_references(session: Session, course_id: int) -> dict[str, list[RefEntry]]:
    """Scannt alle Aufgaben & Materialien des Kurses auf /media/{id}/<datei>-Referenzen.

    Returns: {filename: [RefEntry, ...]} (pro Quelle dedupliziert;
    count = wie oft in derselben Quelle referenziert)
    """
    pattern = _reference_pattern(course_id)
    refs: dict[str, dict[str, RefEntry]] = {}

    def add(fname: str, task_id: Optional[int], material_id: Optional[int], location: str) -> None:
        bucket = refs.setdefault(fname, {})
        # Skript-Kapitel haben kein material_id — der Location-String
        # ("Skript: <Titel>") unterscheidet sie voneinander.
        key = f"task:{task_id}" if task_id else f"material:{material_id}|{location}"
        if key in bucket:
            bucket[key]["count"] += 1
        else:
            bucket[key] = {
                "task_id": task_id,
                "material_id": material_id,
                "location": location,
                "count": 1,
            }

    for t in session.exec(select(Task).where(Task.course_id == course_id)).all():
        for fname in pattern.findall(t.description or ""):
            add(fname, t.id, None, f"Aufgabe: {t.title}")

    for mat in session.exec(
        select(CourseMaterial).where(CourseMaterial.course_id == course_id)
    ).all():
        kind = "Skript" if mat.material_type == MaterialType.SCRIPT else "Slides"
        for fname in pattern.findall(mat.content or ""):
            add(fname, None, mat.id, f"{kind}: {mat.title}")

    for s in session.exec(
        select(ScriptSection).where(ScriptSection.course_id == course_id)
    ).all():
        for fname in pattern.findall(s.content or ""):
            add(fname, None, None, f"Skript: {s.title}")

    return {fname: list(bucket.values()) for fname, bucket in refs.items()}


def sync_media_usages(session: Session, course_id: int) -> None:
    """Rebaut die MediaUsage-Einträge eines Kurses aus dem Markdown-Inhalt.

    Aufrufen nach: Task/Material create/update/delete/duplicate + Media delete.
    """
    media_list = session.exec(
        select(CourseMedia).where(CourseMedia.course_id == course_id)
    ).all()
    if media_list:
        for u in session.exec(
            select(MediaUsage).where(MediaUsage.media_id.in_([m.id for m in media_list]))  # type: ignore[attr-defined]
        ).all():
            session.delete(u)

    by_filename = {m.file_path.rsplit("/", 1)[-1]: m for m in media_list}
    for fname, entries in _scan_references(session, course_id).items():
        media = by_filename.get(fname)
        if not media:
            continue  # Referenz auf unbekanntes Medium (z.B. gelöscht)
        for e in entries:
            session.add(
                MediaUsage(
                    media_id=media.id,  # type: ignore[arg-type]
                    task_id=e["task_id"],
                    material_id=e["material_id"],
                    location=e["location"],
                )
            )
    session.commit()


def reference_counts(session: Session, course_id: int) -> dict[str, RefInfo]:
    """Zusätzliche Referenz-Infos pro Datei für die Medien-Übersicht.

    Returns: {filename: {"total": n, "duplicates": [location, ...]}}
    (duplicates = Quellen, in denen das Medium mehrfach vorkommt)
    """
    result: dict[str, RefInfo] = {}
    for fname, entries in _scan_references(session, course_id).items():
        total = sum(e["count"] for e in entries)
        duplicates = [e["location"] for e in entries if e["count"] > 1]
        result[fname] = {"total": total, "duplicates": duplicates}
    return result


def unused_media_for_script(session: Session, course_id: int) -> list[dict]:
    """Sichtbare Medien, die in keinem Skript-Kapitel referenziert sind.

    Für den LLM-Prompt, damit das LLM passende Medien einbinden kann.
    Returns: [{"title", "description", "url"}]
    """
    pattern = _reference_pattern(course_id)
    used: set[str] = set()
    for s in session.exec(
        select(ScriptSection).where(ScriptSection.course_id == course_id)
    ).all():
        used.update(pattern.findall(s.content or ""))

    result = []
    for m in session.exec(
        select(CourseMedia).where(CourseMedia.course_id == course_id)
    ).all():
        fname = m.file_path.rsplit("/", 1)[-1]
        if fname in used:
            continue
        result.append({
            "title": m.title,
            "description": (m.llm_description or "").strip(),
            "url": media_url(m),
        })
    return result
