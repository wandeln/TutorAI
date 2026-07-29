"""
Admin-Endpoints: Kurs-Management + User-Verwaltung + Systemeinstellungen.

Rollen: Administrator (global)
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    Course,
    CourseCreate,
    CourseSettings,
    CourseSettingsUpdate,
    GlobalUserRole,
    CourseRole,
    User,
    UserCourse,
)
from services.auth_service import hash_password, require_global_admin

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ═══════════════════════════════════════════════════════════════════
# KURSE
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses")
async def list_courses(
    session: Session = Depends(get_session),
    user: User = Depends(require_global_admin()),
):
    """Alle Kurse auflisten."""
    courses = session.exec(select(Course)).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "semester": c.semester,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "member_count": len(c.course_members),
        }
        for c in courses
    ]


@router.post("/courses")
async def create_course(
    data: CourseCreate,
    session: Session = Depends(get_session),
    user: User = Depends(require_global_admin()),
):
    """Neuen Kurs erstellen."""
    course = Course(
        name=data.name,
        description=data.description,
        semester=data.semester,
        created_by=user.id,  # type: ignore[arg-type]
    )
    session.add(course)
    session.commit()
    session.refresh(course)
    
    # Admin automatisch als Kurs-Member mit PROF-Rolle hinzufügen
    membership = UserCourse(
        user_id=user.id,  # type: ignore[arg-type]
        course_id=course.id,  # type: ignore[arg-type]
        role_in_course=CourseRole.PROF,
    )
    session.add(membership)
    session.commit()
    
    return {
        "message": f"Kurs '{course.name}' erstellt.",
        "course": {
            "id": course.id,
            "name": course.name,
            "semester": course.semester,
        },
    }


@router.delete("/courses/{course_id}")
async def delete_course(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_global_admin()),
):
    """Kurs löschen (inkl. aller Aufgaben und Einreichungen)."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")
    
    # TODO: Cascading deletes oder manuell löschen
    session.delete(course)
    session.commit()
    return {"message": f"Kurs '{course.name}' gelöscht."}


# ═══════════════════════════════════════════════════════════════════
# USER-MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    session: Session = Depends(get_session),
    user: User = Depends(require_global_admin()),
):
    """Alle User auflisten."""
    users = session.exec(select(User)).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "email": u.email,
            "role": u.role.value,
        }
        for u in users
    ]


@router.post("/users")
async def create_user(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(require_global_admin()),
):
    """Neuen User erstellen (Admin-only). Always GlobalUserRole.USER."""
    body = await request.json()
    
    username = body.get("username", "").strip()
    email = body.get("email", "").strip()
    name = body.get("name", "").strip()
    password = body.get("password", "")
    
    if not all([username, email, name]):
        raise HTTPException(400, "Username, Email und Name erforderlich.")
    
    # Exists-Check
    existing = session.exec(
        select(User).where(User.username == username)
    ).first()
    if existing:
        raise HTTPException(400, f"Username '{username}' existiert bereits.")
    
    pw_hash = hash_password(password) if password else None
    
    new_user = User(
        username=username,
        email=email,
        name=name,
        role=GlobalUserRole.USER,
        password_hash=pw_hash,
    )
    
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    return {
        "message": f"User '{username}' erstellt.",
        "user": {
            "id": new_user.id,
            "username": new_user.username,
            "name": new_user.name,
            "role": new_user.role.value,
        },
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """User-Daten aktualisieren (Name, Email, Password, globale Rolle)."""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User nicht gefunden.")
    
    body = await request.json()
    
    if "name" in body:
        user.name = body["name"]
    if "email" in body:
        user.email = body["email"]
    if "role" in body:
        # Erlaube Änderung der globalen Rolle (z.B. USER -> ADMIN oder umgekehrt)
        user.role = GlobalUserRole(body["role"].upper())
    if "password" in body and body["password"]:
        user.password_hash = hash_password(body["password"])
    
    session.add(user)
    session.commit()
    session.refresh(user)
    
    return {
        "message": f"User '{user.username}' aktualisiert.",
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "email": user.email,
            "role": user.role.value,
        },
    }


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """User löschen."""
    if user_id == current_user.id:
        raise HTTPException(400, "Du kannst dich selbst nicht löschen.")
    
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User nicht gefunden.")
    
    session.delete(user)
    session.commit()
    return {"message": f"User '{user.username}' gelöscht."}


# ═══════════════════════════════════════════════════════════════════
# KURS-MITGLIEDER (Admin)
# ═══════════════════════════════════════════════════════════════════

@router.post("/courses/{course_id}/members")
async def add_members_to_course(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """
    User zu einem Kurs hinzufügen (Admin-only).
    
    Request:
        {
            "user_ids": [1, 2, 3],
            "role_in_course": "prof" | "tutor" | "student"
        }
    """
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")
    
    body = await request.json()
    user_ids = body.get("user_ids", [])
    role_in_course = CourseRole(body.get("role_in_course", "STUDENT").upper())
    
    added = []
    for uid in user_ids:
        user = session.get(User, uid)
        if not user:
            continue
        
        # Prüfen, ob schon Mitglied
        existing = session.exec(
            select(UserCourse)
            .where(UserCourse.user_id == uid)
            .where(UserCourse.course_id == course_id)
        ).first()
        
        if existing:
            existing.role_in_course = role_in_course
            session.add(existing)
        else:
            membership = UserCourse(
                user_id=uid,
                course_id=course_id,
                role_in_course=role_in_course,
            )
            session.add(membership)
        
        added.append({"user_id": uid, "username": user.username})
    
    session.commit()
    
    return {
        "message": f"{len(added)} Mitglieder hinzugefügt.",
        "added": added,
    }


@router.get("/courses/{course_id}/members")
async def list_course_members(
    course_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """Alle Mitglieder eines Kurses auflisten."""
    members = session.exec(
        select(UserCourse).where(UserCourse.course_id == course_id)
    ).all()
    
    return [
        {
            "id": m.id,
            "user_id": m.user_id,
            "username": m.user.username if m.user else "unknown",
            "name": m.user.name if m.user else "unknown",
            "role_in_course": m.role_in_course.value,
        }
        for m in members
    ]


@router.delete("/courses/{course_id}/members/{user_id}")
async def remove_member_from_course(
    course_id: int,
    user_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """Mitglied aus Kurs entfernen."""
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user_id)
        .where(UserCourse.course_id == course_id)
    ).first()
    
    if not membership:
        raise HTTPException(404, "Mitgliedschaft nicht gefunden.")
    
    session.delete(membership)
    session.commit()
    return {"message": "Mitglied entfernt."}


# ═══════════════════════════════════════════════════════════════════
# KURSEINSTELLUNGEN (LLM, LDAP, Prompts)
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/settings")
async def get_course_settings(
    course_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """Kurs-Einstellungen laden (LLM-Config, LDAP, Prompts)."""
    settings = session.exec(
        select(CourseSettings).where(CourseSettings.course_id == course_id)
    ).first()
    
    if not settings:
        settings = CourseSettings(course_id=course_id)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    
    return {
        "id": settings.id,
        "course_id": settings.course_id,
        "llm_api_url": settings.llm_api_url,
        "llm_model": settings.llm_model,
        "grading_prompt": settings.grading_prompt,
        "use_ldap": settings.use_ldap,
        "ldap_server": settings.ldap_server,
        "ldap_base_dn": settings.ldap_base_dn,
    }


@router.put("/courses/{course_id}/settings")
async def update_course_settings(
    course_id: int,
    data: CourseSettingsUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_global_admin()),
):
    """Kurs-Einstellungen aktualisieren."""
    settings = session.exec(
        select(CourseSettings).where(CourseSettings.course_id == course_id)
    ).first()
    
    if not settings:
        settings = CourseSettings(course_id=course_id)
    
    # Nur nicht-None Werte updaten
    update_data = data.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(settings, key, value)
    
    session.add(settings)
    session.commit()
    session.refresh(settings)
    
    return {"message": "Einstellungen aktualisiert.", "settings": {
        "id": settings.id,
        "use_ldap": settings.use_ldap,
        "llm_model": settings.llm_model,
    }}