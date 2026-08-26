"""
Kurs-Forum: Chat in Kanälen pro Kurs.

Alle Kurs-Rollen (Student/Tutor/PROF) können in allen Kanälen lesen und
schreiben. Kanäle anlegen dürfen alle Mitglieder; umbenennen/löschen nur
der Ersteller oder Tutor/PROF. Nachrichten löschen: eigene für alle,
beliebige für Tutor/PROF.

Kanäle:
- GET    /courses/{course_id}/forum/channels
- POST   /courses/{course_id}/forum/channels
- PATCH  /courses/{course_id}/forum/channels/{channel_id}
- DELETE /courses/{course_id}/forum/channels/{channel_id}

Nachrichten (im Kanal):
- GET    /courses/{course_id}/forum/channels/{channel_id}/messages
         - ?after_id=N: nur Nachrichten neuer als N (für Polling)
- POST   /courses/{course_id}/forum/channels/{channel_id}/messages
- DELETE /courses/{course_id}/forum/channels/{channel_id}/messages/{message_id}
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, func, select

from database.base import get_session
from database.models import (
    CourseRole,
    ForumChannel,
    ForumChannelCreate,
    ForumChannelUpdate,
    ForumMessage,
    ForumMessageCreate,
    GlobalUserRole,
    User,
    UserCourse,
)
from services.auth_service import require_course_access

router = APIRouter(prefix="/api", tags=["Forum"])

# Forum ist für alle Kurs-Mitglieder (Student/Tutor/PROF) + Admins offen.
_ALL_COURSE_ROLES = (CourseRole.PROF, CourseRole.TUTOR, CourseRole.STUDENT)

FORUM_PAGE_SIZE = 200

DEFAULT_CHANNEL_NAME = "Allgemein"


def _role_in_course(session: Session, user: User, course_id: int) -> Optional[str]:
    """Rolle des Users im Kurs (als String) oder None bei keiner Membership."""
    uc = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if uc:
        return uc.role_in_course.value
    return "ADMIN" if user.role == GlobalUserRole.ADMIN else None


def _is_staff(session: Session, user: User, course_id: int) -> bool:
    """Admin (global) oder Tutor/PROF im Kurs."""
    if user.role == GlobalUserRole.ADMIN:
        return True
    return _role_in_course(session, user, course_id) in (
        CourseRole.PROF.value,
        CourseRole.TUTOR.value,
    )


def _get_channel(session: Session, course_id: int, channel_id: int) -> ForumChannel:
    """Kanal im Kurs laden oder 404."""
    ch = session.exec(
        select(ForumChannel)
        .where(ForumChannel.id == channel_id)
        .where(ForumChannel.course_id == course_id)
    ).first()
    if not ch:
        raise HTTPException(404, "Kanal nicht gefunden.")
    return ch


def _channel_dict(
    ch: ForumChannel,
    last_message_at: Optional[datetime],
    can_manage: bool,
) -> dict[str, Any]:
    """Kanal als API-/Template-Dict (inkl. letzter Nachricht + Rechte)."""
    return {
        "id": ch.id,
        "name": ch.name,
        "description": ch.description,
        "can_manage": can_manage,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "last_message_label": last_message_at.strftime("%d.%m.%y") if last_message_at else "",
    }


def _message_dict(
    msg: ForumMessage,
    sender: Optional[User],
    role: str,
    can_delete: bool,
) -> dict[str, Any]:
    """Eine Nachricht als Template-/API-JSON (inkl. Sender-Infos)."""
    return {
        "id": msg.id,
        "channel_id": msg.channel_id,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
        "user_id": msg.user_id,
        "username": sender.username if sender else "unknown",
        "name": sender.name if sender else "unknown",
        "role": role,
        "avatar": f"/avatars/{sender.avatar.rsplit('/', 1)[-1]}" if sender and sender.avatar else None,
        "can_delete": can_delete,
    }


def load_channels_payload(
    session: Session,
    course_id: int,
    viewer: User,
    viewer_role: Optional[str],
) -> list[dict[str, Any]]:
    """Alle Kanäle des Kurses (Anlage-Reihenfolge) + letzte Nachricht pro Kanal."""
    channels = session.exec(
        select(ForumChannel)
        .where(ForumChannel.course_id == course_id)
        .order_by(ForumChannel.id)  # type: ignore[call-overload]
    ).all()

    last_msgs = dict(
        session.exec(
            select(ForumMessage.channel_id, func.max(ForumMessage.created_at))
            .where(ForumMessage.course_id == course_id)
            .group_by(ForumMessage.channel_id)  # type: ignore[call-overload]
        ).all()
    )

    is_staff = viewer.role == GlobalUserRole.ADMIN or viewer_role in (
        CourseRole.PROF.value,
        CourseRole.TUTOR.value,
    )
    return [
        _channel_dict(
            ch,
            last_msgs.get(ch.id),
            can_manage=is_staff or ch.created_by == viewer.id,
        )
        for ch in channels
    ]


def load_forum_payload(
    session: Session,
    course_id: int,
    channel_id: int,
    viewer: User,
    viewer_role: Optional[str],
    after_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Lädt Forum-Nachrichten eines Kanals chronologisch + Sender-Infos (ohne N+1).

    after_id: None → letzte FORUM_PAGE_SIZE Nachrichten (Initial-Load),
              sonst nur Nachrichten mit id > after_id (Polling).
    """
    msgs = session.exec(
        select(ForumMessage)
        .where(ForumMessage.course_id == course_id)
        .where(ForumMessage.channel_id == channel_id)
        .order_by(ForumMessage.id.desc())  # type: ignore[attr-defined]
    ).all()
    msgs = list(reversed(msgs))  # chronologisch (älteste zuerst)
    if after_id is not None:
        msgs = [m for m in msgs if m.id > after_id]
    else:
        msgs = msgs[-FORUM_PAGE_SIZE:]

    user_ids = {m.user_id for m in msgs}
    if not user_ids:
        return []

    users = {
        u.id: u
        for u in session.exec(select(User).where(User.id.in_(user_ids))).all()  # type: ignore[attr-defined]
    }
    roles = {
        uc.user_id: uc.role_in_course.value
        for uc in session.exec(
            select(UserCourse)
            .where(UserCourse.course_id == course_id)
            .where(UserCourse.user_id.in_(user_ids))  # type: ignore[attr-defined]
        ).all()
    }

    is_staff = viewer.role == GlobalUserRole.ADMIN or viewer_role in (CourseRole.PROF.value, CourseRole.TUTOR.value)
    return [
        _message_dict(
            m,
            users.get(m.user_id),
            roles.get(m.user_id) or "STUDENT",
            can_delete=is_staff or m.user_id == viewer.id,
        )
        for m in msgs
    ]


# ─── Kanäle ────────────────────────────────────────────────────────


@router.get("/courses/{course_id}/forum/channels")
async def list_forum_channels(
    course_id: int,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Alle Kanäle des Kurses (inkl. letzter Nachricht pro Kanal)."""
    viewer, _ = viewer_and_course
    role = _role_in_course(session, viewer, course_id)
    return load_channels_payload(session, course_id, viewer, role)


@router.post("/courses/{course_id}/forum/channels", status_code=201)
async def create_forum_channel(
    course_id: int,
    data: ForumChannelCreate,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Neuen Forum-Kanal anlegen (alle Kurs-Mitglieder dürfen)."""
    viewer, _ = viewer_and_course
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Kanal-Name darf nicht leer sein.")
    if len(name) > 100:
        raise HTTPException(400, "Kanal-Name darf maximal 100 Zeichen haben.")

    existing = session.exec(
        select(ForumChannel)
        .where(ForumChannel.course_id == course_id)
        .where(ForumChannel.name == name)
    ).first()
    if existing:
        raise HTTPException(409, f"Kanal '{name}' existiert bereits.")

    ch = ForumChannel(
        course_id=course_id,
        name=name,
        description=data.description.strip(),
        created_by=viewer.id,  # type: ignore[arg-type]
    )
    session.add(ch)
    session.commit()
    session.refresh(ch)

    return _channel_dict(ch, None, can_manage=True)


@router.patch("/courses/{course_id}/forum/channels/{channel_id}")
async def update_forum_channel(
    course_id: int,
    channel_id: int,
    data: ForumChannelUpdate,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Kanal umbenennen/Beschreibung ändern (Ersteller oder Tutor/PROF)."""
    viewer, _ = viewer_and_course
    ch = _get_channel(session, course_id, channel_id)

    is_staff = _is_staff(session, viewer, course_id)
    if not is_staff and ch.created_by != viewer.id:
        raise HTTPException(403, "Nur der Ersteller (oder Tutor/PROF) kann den Kanal umbenennen.")

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(400, "Kanal-Name darf nicht leer sein.")
        if name != ch.name:
            clash = session.exec(
                select(ForumChannel)
                .where(ForumChannel.course_id == course_id)
                .where(ForumChannel.name == name)
                .where(ForumChannel.id != channel_id)
            ).first()
            if clash:
                raise HTTPException(409, f"Kanal '{name}' existiert bereits.")
        ch.name = name
    if data.description is not None:
        ch.description = data.description.strip()

    session.add(ch)
    session.commit()
    session.refresh(ch)

    return _channel_dict(ch, None, can_manage=True)


@router.delete("/courses/{course_id}/forum/channels/{channel_id}")
async def delete_forum_channel(
    course_id: int,
    channel_id: int,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Kanal inkl. aller Nachrichten löschen (Ersteller oder Tutor/PROF)."""
    viewer, _ = viewer_and_course
    ch = _get_channel(session, course_id, channel_id)

    is_staff = _is_staff(session, viewer, course_id)
    if not is_staff and ch.created_by != viewer.id:
        raise HTTPException(403, "Nur der Ersteller (oder Tutor/PROF) kann den Kanal löschen.")

    msg_count = session.exec(
        select(ForumMessage)
        .where(ForumMessage.course_id == course_id)
        .where(ForumMessage.channel_id == channel_id)
    ).all()
    for m in msg_count:
        session.delete(m)
    session.delete(ch)
    session.commit()
    return {"message": f"Kanal '{ch.name}' gelöscht."}


# ─── Nachrichten (pro Kanal) ───────────────────────────────────────


@router.get("/courses/{course_id}/forum/channels/{channel_id}/messages")
async def list_forum_messages(
    course_id: int,
    channel_id: int,
    after_id: Optional[int] = Query(None, ge=0, description="Nur Nachrichten neuer als diese ID (Polling)"),
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Forum-Nachrichten des Kanals (chronologisch, älteste zuerst)."""
    viewer, _ = viewer_and_course
    _get_channel(session, course_id, channel_id)
    role = _role_in_course(session, viewer, course_id)
    return load_forum_payload(session, course_id, channel_id, viewer, role, after_id)


@router.post("/courses/{course_id}/forum/channels/{channel_id}/messages", status_code=201)
async def create_forum_message(
    course_id: int,
    channel_id: int,
    data: ForumMessageCreate,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Neue Forum-Nachricht im Kanal schreiben."""
    viewer, _ = viewer_and_course
    ch = _get_channel(session, course_id, channel_id)
    content = data.content.strip()
    if not content:
        raise HTTPException(400, "Nachricht darf nicht leer sein.")

    msg = ForumMessage(
        course_id=course_id,
        channel_id=ch.id,
        user_id=viewer.id,  # type: ignore[arg-type]
        content=content,
    )
    session.add(msg)
    session.commit()
    session.refresh(msg)

    role = _role_in_course(session, viewer, course_id) or "STUDENT"
    return _message_dict(msg, viewer, role, can_delete=True)


@router.delete("/courses/{course_id}/forum/channels/{channel_id}/messages/{message_id}")
async def delete_forum_message(
    course_id: int,
    channel_id: int,
    message_id: int,
    session: Session = Depends(get_session),
    viewer_and_course: tuple[User, int] = Depends(require_course_access(*_ALL_COURSE_ROLES)),
):
    """Forum-Nachricht löschen (eigene; Tutor/PROF dürfen alle löschen)."""
    viewer, _ = viewer_and_course
    _get_channel(session, course_id, channel_id)
    msg = session.exec(
        select(ForumMessage)
        .where(ForumMessage.id == message_id)
        .where(ForumMessage.course_id == course_id)
        .where(ForumMessage.channel_id == channel_id)
    ).first()
    if not msg:
        raise HTTPException(404, "Nachricht nicht gefunden.")

    is_staff = _is_staff(session, viewer, course_id)
    if not is_staff and msg.user_id != viewer.id:
        raise HTTPException(403, "Nur eigene Nachrichten löschen (oder als Tutor/PROF).")

    session.delete(msg)
    session.commit()
    return {"message": "Nachricht gelöscht."}
