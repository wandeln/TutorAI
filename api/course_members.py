"""
Kurs-Mitglieder-Management für PROFs.

Eine PROF kann Mitglieder ihres Kurses hinzufügen, Rollen ändern
oder entfernen — ohne globale Admin-Rechte.

Diese Endpunkte sind unter /api/courses/{course_id}/members erreichbar.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    Course,
    CourseRole,
    User,
    UserCourse,
)
from services.auth_service import require_course_access

router = APIRouter(tags=["Kurs-Mitglieder (PROF)"])


@router.post("/courses/{course_id}/members")
async def add_members_to_course(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple = Depends(require_course_access(CourseRole.PROF)),
):
    """
    User zu einem Kurs hinzufügen (PROF-only).
    
    Request:
        {
            "user_ids": [1, 2, 3],
            "role_in_course": "prof" | "tutor" | "student"
        }
    """
    user, _ = user_and_course
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    body = await request.json()
    user_ids = body.get("user_ids", [])
    role_in_course_str = body.get("role_in_course", "STUDENT").upper()

    # Eine PROF kann keine andere PROF erstellen
    if role_in_course_str == "PROF":
        raise HTTPException(
            403,
            "Du kannst keine weitere PROF zu diesem Kurs ernennen. "
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
    user_and_course: tuple = Depends(require_course_access(CourseRole.PROF)),
):
    """Alle Mitglieder eines Kurses auflisten (PROF-only)."""
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
    user_and_course: tuple = Depends(require_course_access(CourseRole.PROF)),
):
    """Mitglied aus Kurs entfernen (PROF-only)."""
    user, _ = user_and_course
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user_id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(404, "Mitgliedschaft nicht gefunden.")

    # Eine PROF kann sich selbst nicht entfernen
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
    user_and_course: tuple = Depends(require_course_access(CourseRole.PROF)),
):
    """
    Rolle eines Kursmitglieds ändern (PROF-only).
    
    Request:
        {
            "role_in_course": "tutor" | "student"
        }
    
    Eine PROF kann keine andere PROF erstellen.
    """
    user, _ = user_and_course
    body = await request.json()
    role_in_course_str = body.get("role_in_course", "").upper()

    if role_in_course_str == "PROF":
        raise HTTPException(
            403,
            "Du kannst keine weitere PROF zu diesem Kurs ernennen. "
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
        "message": f"Rolle ge&auml;ndert.",
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
    user_and_course: tuple = Depends(require_course_access(CourseRole.PROF)),
):
    """
    Kursnamen beschreiben (PROF-only).
    
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