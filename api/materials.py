"""
Kurs-Material: Vorlesungsskript & Slides (Markdown).

Regeln:
- Pro Kurs & Typ (script/slides) existiert maximal ein Material.
- Studenten sehen nur sichtbare Materialien (is_visible=True).
- Anlegen/Bearbeiten: PROF/TUTOR; Löschen: PROF. Admin darf alles.
- Slides = ein Markdown-Dokument, Folien durch eine Zeile `---` getrennt.
- Nach jeder Content-Änderung: sync_media_usages() (Medien-Einbindung).
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    CourseMaterial,
    CourseRole,
    GlobalUserRole,
    MaterialType,
    User,
    UserCourse,
)
from services.auth_service import get_current_user, require_course_access
from services.media_service import sync_media_usages

router = APIRouter(prefix="/api", tags=["Skript & Slides"])

LABELS = {MaterialType.SCRIPT: "Skript", MaterialType.SLIDES: "Slides"}


def _get_membership(session: Session, user: User, course_id: int) -> Optional[UserCourse]:
    if user.role == GlobalUserRole.ADMIN:
        return None  # Admin braucht keine Membership
    return session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()


def _check_member(user: User, session: Session, course_id: int) -> None:
    """Jedes Kurs-Mitglied (oder Admin) darf lesen."""
    if user.role == GlobalUserRole.ADMIN:
        return
    if not _get_membership(session, user, course_id):
        raise HTTPException(403, "Du bist kein Mitglied dieses Kurses.")


def _is_tutor(user: User, session: Session, course_id: int) -> bool:
    if user.role == GlobalUserRole.ADMIN:
        return True
    m = _get_membership(session, user, course_id)
    return bool(m and m.role_in_course in (CourseRole.PROF, CourseRole.TUTOR))


def _get_material(session: Session, course_id: int, material_id: int) -> CourseMaterial:
    material = session.get(CourseMaterial, material_id)
    if not material or material.course_id != course_id:
        raise HTTPException(404, "Material nicht gefunden.")
    return material


def _material_to_dict(m: CourseMaterial, include_content: bool = False) -> dict:
    d = {
        "id": m.id,
        "title": m.title,
        "material_type": m.material_type.value,
        "is_visible": m.is_visible,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }
    if include_content:
        d["content"] = m.content
    return d


# ─── Lesen (alle Mitglieder) ─────────────────────────────────────

@router.get("/courses/{course_id}/materials")
async def list_materials(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Materialien des Kurses (Studenten nur sichtbare)."""
    _check_member(user, session, course_id)
    is_tutor = _is_tutor(user, session, course_id)

    materials = session.exec(
        select(CourseMaterial).where(CourseMaterial.course_id == course_id)
    ).all()
    return [
        _material_to_dict(m)
        for m in materials
        if is_tutor or m.is_visible
    ]


@router.get("/courses/{course_id}/materials/{material_id}")
async def get_material(
    course_id: int,
    material_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelnes Material inkl. Markdown-Content."""
    _check_member(user, session, course_id)
    material = _get_material(session, course_id, material_id)

    is_tutor = _is_tutor(user, session, course_id)
    if not material.is_visible and not is_tutor:
        raise HTTPException(404, "Material nicht gefunden.")

    return _material_to_dict(material, include_content=True)


# ─── Schreiben (PROF/TUTOR) ──────────────────────────────────────

@router.post("/courses/{course_id}/materials")
async def create_material(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Skript oder Slides anlegen (max. eins pro Typ)."""
    user, _ = user_and_course
    body = await request.json()

    try:
        mtype = MaterialType(body.get("material_type"))
    except ValueError:
        raise HTTPException(400, "material_type muss 'script' oder 'slides' sein.")

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Titel darf nicht leer sein.")

    existing = session.exec(
        select(CourseMaterial)
        .where(CourseMaterial.course_id == course_id)
        .where(CourseMaterial.material_type == mtype)
    ).first()
    if existing:
        raise HTTPException(409, f"Es existiert bereits ein {LABELS[mtype]} für diesen Kurs.")

    material = CourseMaterial(
        course_id=course_id,
        title=title,
        material_type=mtype,
        content=body.get("content") or "",
        is_visible=bool(body.get("is_visible", True)),
        created_by=user.id,  # type: ignore[arg-type]
    )
    session.add(material)
    session.commit()
    session.refresh(material)
    sync_media_usages(session, course_id)

    return {"message": f"{LABELS[mtype]} '{material.title}' erstellt.", "material": _material_to_dict(material)}


@router.put("/courses/{course_id}/materials/{material_id}")
async def update_material(
    course_id: int,
    material_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Skript/Slides aktualisieren (Titel, Content, Sichtbarkeit)."""
    material = _get_material(session, course_id, material_id)
    body = await request.json()

    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Titel darf nicht leer sein.")
        material.title = title
    if "content" in body:
        material.content = body.get("content") or ""
    if "is_visible" in body:
        material.is_visible = bool(body["is_visible"])

    material.updated_at = datetime.now(timezone.utc)
    session.add(material)
    session.commit()
    session.refresh(material)
    sync_media_usages(session, course_id)

    return {"message": f"{LABELS[material.material_type]} aktualisiert.", "material": _material_to_dict(material)}


@router.delete("/courses/{course_id}/materials/{material_id}")
async def delete_material(
    course_id: int,
    material_id: int,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF)),
):
    """Skript/Slides löschen (nur PROF)."""
    material = _get_material(session, course_id, material_id)
    label = LABELS[material.material_type]
    title = material.title

    session.delete(material)
    session.commit()
    sync_media_usages(session, course_id)

    return {"message": f"{label} '{title}' gelöscht."}
