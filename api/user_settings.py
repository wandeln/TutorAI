"""
User-Settings: Name / Passwort ändern.

- PATCH /api/auth/settings  → Eigene Einstellungen aktualisieren
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from database.base import get_session
from database.models import User, GlobalUserRole
from services.auth_service import (
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth/settings", tags=["User-Settings"])


class SettingsUpdate(BaseModel):
    current_password: str = Field(default="", max_length=128)
    new_name: str = Field(default="", max_length=200)
    new_password: str = Field(default="", max_length=128)


@router.patch("/")
async def update_settings(
    data: SettingsUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Eigene Einstellungen aktualisieren.

    - `new_name` → Name ändern (kein Passwort nötig)
    - `new_password` → Passwort ändern (aktuelles Passwort als Bestätigung nötig)
    """
    name_changed = False
    password_changed = False

    # ─── Name ändern ───────────────────────────────────────────
    if data.new_name:
        stripped = data.new_name.strip()
        if not stripped:
            raise HTTPException(400, "Name darf nicht leer sein.")
        if stripped != user.name:
            user.name = stripped
            name_changed = True

    # ─── Passwort ändern ───────────────────────────────────────
    if data.new_password:
        if not data.current_password:
            raise HTTPException(
                400,
                "Aktuelles Password ist erforderlich, um das Password zu ändern.",
            )

        # LDAP-User haben keinen DB-Password-Hash
        if not user.password_hash:
            raise HTTPException(
                400,
                "Dein Account verwendet LDAP-Authentifizierung. "
                "Passwortänderungen sind hier nicht möglich.",
            )

        if not verify_password(data.current_password, user.password_hash):
            raise HTTPException(
                401,
                "Das aktuelle Password ist falsch.",
            )

        if data.new_password != data.current_password:
            user.password_hash = hash_password(data.new_password)
            password_changed = True

    if not name_changed and not password_changed:
        return {"message": "Keine Änderungen vorgenommen."}

    session.add(user)
    session.commit()
    session.refresh(user)

    messages = []
    if name_changed:
        messages.append("Name wurde aktualisiert.")
    if password_changed:
        messages.append("Password wurde geändert.")

    display_role = "Admin" if user.role == GlobalUserRole.ADMIN else "User"
    return {
        "message": " ".join(messages),
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "role": display_role,
        },
    }