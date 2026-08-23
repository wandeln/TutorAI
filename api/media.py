"""
Medien-Endpoints: Upload, Liste, Bearbeiten, Löschen (je Kurs).

Rollen: PROF (im Kurs) + Admin. Die Tab-Seite ist nur für PROF/Admin sichtbar;
Tutoren erhalten Medien über PROF (Berechtigung ggf. später erweiterbar).

Dateien landen in data/media/course_{id}/ mit UUID-Namen.
Der Versand läuft über die authentifizierte Route GET /media/{course_id}/{filename}
(siehe main.py) — kein Static-Mount, damit Medien versteckter Inhalte
nicht ohne Kurs-Membership abrufbar sind.
"""

import base64
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlmodel import Session, select

from database.base import get_session
from database.models import CourseMedia, CourseRole, MediaUsage, User
from services.auth_service import require_course_access
from services.llm_service import LLMService
from services.settings_resolver import get_effective_llm_config
from services import media_service
from services.media_service import RefInfo

router = APIRouter(prefix="/api", tags=["Medien"])
llm_service = LLMService()


def _get_media(session: Session, course_id: int, media_id: int) -> CourseMedia:
    media = session.get(CourseMedia, media_id)
    if not media or media.course_id != course_id:
        raise HTTPException(404, "Medium nicht gefunden.")
    return media


def _media_to_dict(
    m: CourseMedia,
    usages: list[MediaUsage],
    counts: dict[str, RefInfo],
) -> dict:
    fname = m.file_path.rsplit("/", 1)[-1]
    c = counts.get(fname, {"total": 0, "duplicates": []})
    return {
        "id": m.id,
        "title": m.title,
        "url": media_service.media_url(m),
        "media_type": m.media_type,
        "mime_type": m.mime_type,
        "file_size": m.file_size,
        "llm_description": m.llm_description,
        "usages": [
            {"location": u.location, "task_id": u.task_id, "material_id": u.material_id}
            for u in usages
        ],
        "ref_count": c["total"],
        "duplicate_in": c["duplicates"],
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _load_media_payload(session: Session, course_id: int) -> list[dict]:
    media_list = session.exec(
        select(CourseMedia)
        .where(CourseMedia.course_id == course_id)
        .order_by(CourseMedia.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    if not media_list:
        return []
    usages = session.exec(
        select(MediaUsage).where(MediaUsage.media_id.in_([m.id for m in media_list]))  # type: ignore[attr-defined]
    ).all()
    counts = media_service.reference_counts(session, course_id)
    return [
        _media_to_dict(m, [u for u in usages if u.media_id == m.id], counts)
        for m in media_list
    ]


@router.get("/courses/{course_id}/media")
async def list_media(
    course_id: int,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Alle Medien des Kurses inkl. Verwendungs-Orten."""
    return _load_media_payload(session, course_id)


@router.post("/courses/{course_id}/media")
async def upload_media(
    course_id: int,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    llm_description: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Medium hochladen (PNG/JPG/WebP/GIF, max. 5 MB)."""
    user, _ = user_and_course
    data = await file.read()
    rel_path, mime, size = media_service.save_upload(course_id, file.filename or "upload.png", data)

    default_title = (file.filename or "Medium").rsplit(".", 1)[0]
    media = CourseMedia(
        course_id=course_id,
        title=(title or "").strip() or default_title,
        file_path=rel_path,
        media_type="image",
        mime_type=mime,
        file_size=size,
        llm_description=(llm_description or "").strip() or None,
        created_by=user.id,  # type: ignore[arg-type]
    )
    session.add(media)
    session.commit()
    session.refresh(media)

    counts = media_service.reference_counts(session, course_id)
    return {
        "message": f"Medium '{media.title}' hochgeladen.",
        "media": _media_to_dict(media, [], counts),
    }


@router.put("/courses/{course_id}/media/{media_id}/file")
async def replace_media_file(
    course_id: int,
    media_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Ersetzt die Datei eines Mediums (gleicher Pfad → Markdown-Referenzen bleiben gültig)."""
    media = _get_media(session, course_id, media_id)
    data = await file.read()
    rel_path, mime, size = media_service.replace_file(media.file_path, file.filename or "upload.png", data)

    media.file_path = rel_path
    media.mime_type = mime
    media.file_size = size
    session.add(media)
    session.commit()
    session.refresh(media)

    counts = media_service.reference_counts(session, course_id)
    return {"message": "Datei ersetzt.", "media": _media_to_dict(media, [], counts)}


@router.post("/courses/{course_id}/media/{media_id}/generate-metadata")
async def generate_media_metadata(
    course_id: int,
    media_id: int,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Erzeugt per LLM Titel + Beschreibung für ein vorhandenes Medium und speichert sie."""
    media = _get_media(session, course_id, media_id)
    path = media_service.MEDIA_DIR / media.file_path
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden.")

    image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
    llm_cfg = get_effective_llm_config(session, course_id)
    result = await llm_service.describe_media_image(
        image_base64=image_base64,
        mime_type=media.mime_type or "image/png",
        config=llm_cfg,
    )
    if not result.get("success"):
        raise HTTPException(502, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

    title = result.get("data", {}).get("title", "").strip()
    description = result.get("data", {}).get("description", "").strip()
    if title:
        media.title = title
    if description:
        media.llm_description = description
    session.add(media)
    session.commit()
    session.refresh(media)

    counts = media_service.reference_counts(session, course_id)
    return {
        "message": "Titel und Beschreibung generiert.",
        "media": _media_to_dict(media, [], counts),
    }


@router.patch("/courses/{course_id}/media/{media_id}")
async def update_media(
    course_id: int,
    media_id: int,
    request: Request,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Titel / Beschreibung ändern."""
    media = _get_media(session, course_id, media_id)
    body = await request.json()

    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Titel darf nicht leer sein.")
        media.title = title
    if "llm_description" in body:
        val = body.get("llm_description")
        media.llm_description = (val or "").strip() or None

    session.add(media)
    session.commit()
    session.refresh(media)
    return {"message": "Medium aktualisiert.", "media": _media_to_dict(media, [], {})}


@router.delete("/courses/{course_id}/media/{media_id}")
async def delete_media(
    course_id: int,
    media_id: int,
    session: Session = Depends(get_session),
    _user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Medium löschen (Datei + DB + Usage-Einträge)."""
    media = _get_media(session, course_id, media_id)

    for u in session.exec(select(MediaUsage).where(MediaUsage.media_id == media.id)).all():
        session.delete(u)
    session.delete(media)
    session.commit()

    media_service.delete_file(media.file_path)
    return {"message": f"Medium '{media.title}' gelöscht."}
