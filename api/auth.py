"""
Auth-Endpoints: Login, Logout, Session-Status.

- POST /api/auth/login  → JWT in httpOnly-Cookie setzen
- POST /api/auth/logout → Cookie löschen
- GET  /api/auth/me     → Aktueller User
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select
import logging

from database.base import get_session
from database.models import User, GlobalUserRole, UserCreate
from services.auth_service import (
    authenticate_user, create_access_token, hash_password,
    get_current_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentifizierung"])


class LoginRequest:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Login via Username + Password.
    
    Setzt JWT-Token in httpOnly-Cookie.
    Prüft LDAP (wenn aktiviert) oder DB-Hash.
    """
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    
    if not username or not password:
        raise HTTPException(400, "Username und Password erforderlich.")
    
    # LDAP-Setting via Resolver (global_settings > course_settings > .env)
    from services.settings_resolver import get_effective_ldap_config
    ldap_cfg = get_effective_ldap_config(session)

    user = await authenticate_user(
        username, password, session,
        use_ldap=ldap_cfg["use_ldap"],
        ldap_server=ldap_cfg["ldap_server"],
        ldap_base_dn=ldap_cfg["ldap_base_dn"],
        ldap_bind_dn=ldap_cfg["ldap_bind_dn"],
        ldap_bind_pw=ldap_cfg["ldap_bind_pw"],
        ldap_user_search=ldap_cfg["ldap_user_search"],
    )
    
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Ungültiger Username oder Password.",
        )
    
    # JWT-Token erstellen und in Cookie setzen (kein role im Token)
    token_data = {
        "sub": user.id,
        "username": user.username,
    }
    token = create_access_token(token_data)
    
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=False,  # False für Dev (Browser-JS kann Cookie lesen). In Prod: True + HTTPS!
        secure=False,
        samesite="lax",
        max_age=8 * 3600,
        path="/",
    )
    
    # Rolle anzeigen: Admin bleibt "Admin", User zeigt generisch "User"
    display_role = "Admin" if user.role == GlobalUserRole.ADMIN else "User"
    return {
        "message": "Erfolgreich angemeldet.",
        "user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "role": display_role,
        },
    }


@router.post("/logout")
async def logout(response: Response):
    """Löscht das Session-Cookie."""
    response.delete_cookie(key="access_token", path="/")
    return {"message": "Erfolgreich abgemeldet."}


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Gibt den aktuellen User zurück."""
    display_role = "Admin" if user.role == GlobalUserRole.ADMIN else "User"
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "email": user.email,
        "role": display_role,
    }


@router.post("/register")
async def register(
    data: UserCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),  # Nur Admin kann registrieren
):
    """
    Neue User registrieren (nur für Admin).
    
    Bei LDAP: Nur Username, Email, Name, Role speichern (kein Password).
    """
    # Prüfen, ob Username schon existiert
    existing = session.exec(
        select(User).where(User.username == data.username)
    ).first()
    if existing:
        raise HTTPException(400, f"Username '{data.username}' existiert bereits.")
    
    pw_hash = hash_password(data.plain_password)
    
    new_user = User(
        username=data.username,
        email=data.email,
        name=data.name,
        role=GlobalUserRole.USER,
        password_hash=pw_hash,
    )
    
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    display_role = "Admin" if new_user.role == GlobalUserRole.ADMIN else "User"
    return {
        "message": f"User '{data.username}' erstellt.",
        "user": {
            "id": new_user.id,
            "username": new_user.username,
            "name": new_user.name,
            "role": display_role,
        },
    }