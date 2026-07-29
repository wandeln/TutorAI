"""
Kurs-Mitglieder-Management für PROFs und Admins.

Eine PROF kann Mitglieder ihres Kurses hinzufügen, Rollen ändern
oder entfernen. Ein globaler Administrator hat die gleichen Rechte
und darf zusätzlich PROFs ernennen.

Diese Endpunkte sind unter /api/courses/{course_id}/members erreichbar.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    Course,
    CourseRole,
    GlobalUserRole,
    User,
    UserCourse,
)
from services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["Kurs-Mitglieder (PROF/Admin)"])


# ─── Eigene Dependency: PROF oder global Admin ──────────────────
# require_course_access(CourseRole.PROF) würde zwar Admins durchlassen,
# setzt aber voraus, dass der User auch in der UserCourse-Tabelle steht.
# Ein Admin kann theoretisch jeden Kurs verwalten, auch ohne Membership.

def require_prof_or_admin():
    """Prüft: User ist PROF im Kurs ODER global Admin. Gibt (User, course_id, ist_admin) zurück."""
    async def course_check(
        request: Request,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ):
        course_id = request.path_params.get("course_id")
        if course_id is None:
            raise HTTPException(
                status_code=400,
                detail="Keine course_id im Pfad gefunden.",
            )
        course_id = int(course_id)

        is_admin = user.role == GlobalUserRole.ADMIN

        # Global-Admin: darf alles
        if is_admin:
            return user, course_id, True

        #ansonsten: muss PROF im Kurs sein
        membership = session.exec(
            select(UserCourse)
            .where(UserCourse.user_id == user.id)
            .where(UserCourse.course_id == course_id)
        ).first()

        if not membership:
            raise HTTPException(
                status_code=403,
                detail="Du bist kein Mitglied dieses Kurses.",
            )
        if membership.role_in_course != CourseRole.PROF:
            raise HTTPException(
                status_code=403,
                detail=f"Zugriff verweigert. Deine Rolle im Kurs: {membership.role_in_course.value}",
            )

        return user, course_id, False

    return course_check


@router.post("/courses/{course_id}/members")
async def add_members_to_course(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """
    User zu einem Kurs hinzufügen (PROF oder Admin).

    Request:
        {
            "user_ids": [1, 2, 3],
            "role_in_course": "prof" | "tutor" | "student"
        }

    Nur ein Admin darf PROFs ernennen.
    """
    user, _, is_admin = auth_result
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    body = await request.json()
    user_ids = body.get("user_ids", [])
    role_in_course_str = body.get("role_in_course", "STUDENT").upper()

    # Nur PROF kann keine PROF ernennen — Admin darf
    if role_in_course_str == "PROF" and not is_admin:
        raise HTTPException(
            403,
            "Du kannst keine PROF zu diesem Kurs ernennen. "
            "Nur ein Administrator kann das.",
        )

    role_in_course = CourseRole(role_in_course_str)

    added = []
    for uid in user_ids:
        target_user = session.get(User, uid)
        if not target_user:
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

        added.append({"user_id": uid, "username": target_user.username})

    session.commit()

    return {
        "message": f"{len(added)} Mitglieder hinzugefügt.",
        "added": added,
    }


@router.get("/courses/{course_id}/members")
async def list_course_members(
    course_id: int,
    session: Session = Depends(get_session),
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """Alle Mitglieder eines Kurses auflisten (PROF oder Admin)."""
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
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """Mitglied aus Kurs entfernen (PROF oder Admin)."""
    user, _, is_admin = auth_result
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user_id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(404, "Mitgliedschaft nicht gefunden.")

    # Niemand kann sich selbst entfernen
    if user_id == user.id:
        raise HTTPException(400, "Du kannst dich selbst nicht entfernen.")

    session.delete(membership)
    session.commit()
    return {"message": "Mitglied entfernt."}


@router.put("/courses/{course_id}/members/{user_id}/role")
async def update_member_role(
    course_id: int,
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """
    Rolle eines Kursmitglieds ändern (PROF oder Admin).

    Request:
        {
            "role_in_course": "prof" | "tutor" | "student"
        }

    Nur ein Admin darf PROFs ernennen.
    """
    user, _, is_admin = auth_result
    body = await request.json()
    role_in_course_str = body.get("role_in_course", "").upper()

    if role_in_course_str == "PROF" and not is_admin:
        raise HTTPException(
            403,
            "Du kannst keine PROF zu diesem Kurs ernennen. "
            "Nur ein Administrator kann das.",
        )

    role_in_course = CourseRole(role_in_course_str)

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user_id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(404, "Mitgliedschaft nicht gefunden.")

    membership.role_in_course = role_in_course
    session.add(membership)
    session.commit()

    return {
        "message": "Rolle geändert.",
        "membership": {
            "user_id": membership.user_id,
            "username": membership.user.username if membership.user else "unknown",
            "role_in_course": membership.role_in_course.value,
        },
    }


@router.put("/courses/{course_id}/name")
async def update_course_name(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """
    Kursnamen beschreiben (PROF oder Admin).

    Request:
        {
            "name": "Neuer Kursname",
            "description": "Neue Beschreibung",
        }
    """
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    body = await request.json()
    if "name" in body:
        course.name = body["name"]
    if "description" in body:
        course.description = body["description"]

    session.add(course)
    session.commit()
    session.refresh(course)

    return {
        "message": f"Kurs '{course.name}' aktualisiert.",
        "course": {
            "id": course.id,
            "name": course.name,
            "description": course.description,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# USER-SUCHE (damit PROF/Admin User finden kann, um sie hinzuzufügen)
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/search-users")
async def search_users_for_course(
    course_id: int,
    q: str = "",
    session: Session = Depends(get_session),
    auth_result: tuple = Depends(require_prof_or_admin()),
):
    """
    User durchsuchen, die noch nicht im Kurs sind.

    Query-Parameter:
        q: Suchbegriff (wird gegen username, name und email gematcht)
    """
    # Aktive Mitglieder des Kurses sammeln
    members = session.exec(
        select(UserCourse).where(UserCourse.course_id == course_id)
    ).all()
    member_ids = {m.user_id for m in members}

    # Alle User durchsuchen
    all_users = session.exec(select(User)).all()

    if q:
        q_lower = q.lower()
        all_users = [
            u for u in all_users
            if q_lower in u.username.lower()
            or q_lower in u.name.lower()
            or q_lower in u.email.lower()
        ]

    # Nur User anzeigen, die noch nicht im Kurs sind
    results = [
        {
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "email": u.email,
        }
        for u in all_users
        if u.id not in member_ids
    ]

    return {"users": results}