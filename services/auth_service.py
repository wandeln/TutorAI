"""
Authentifizierungs-Service.

Unterstützt zwei Auth-Modi:
1. DB-Auth: Username + bcrypt-Hashed-Password (Default)
2. LDAP-Auth: Username + Passwort gegen LDAP-Server

JWT-Tokens werden in httpOnly-Cookies gespeichert.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from sqlmodel import Session, select

from config import (
    SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
    LDAP_ENABLED, LDAP_SERVER, LDAP_BASE_DN, LDAP_USER_SEARCH,
)
from database.base import get_session
from database.models import User, GlobalUserRole, CourseRole, UserCourse
import hashlib
import secrets

# ─── Password Hashing (SHA-256 + salt, kein passlib nötig) ─────
def hash_password(password: str) -> str:
    """Hashes ein Password mit SHA-256 + random salt.
    Format: sha256${salt}${hash}
    """
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${hashed}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifiziert ein Password gegen den Hash."""
    try:
        _, salt, stored_hash = hashed_password.split("$")
    except ValueError:
        return False
    computed = hashlib.sha256((salt + plain_password).encode()).hexdigest()
    return secrets.compare_digest(computed, stored_hash)


# ─── JWT Token ──────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Erzeugt JWT-Token.
    
    Wichtig: 'sub' MUSS ein String sein (pyjwt >= 2.0 Requirement).
    """
    to_encode = data.copy()
    # pyjwt >= 2.0: 'sub' must be str
    if "sub" in to_encode and not isinstance(to_encode["sub"], str):
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.now() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    import jwt
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Decodiert JWT-Token.
    Gibt dict mit Payload zurück, oder None bei ungültigem Token.
    """
    import jwt
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # 'sub' kommt als String zurück, konvertieren zu int
        if "sub" in payload:
            try:
                payload["sub"] = int(payload["sub"])
            except (ValueError, TypeError):
                pass
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None


# ─── Login Logic ────────────────────────────────────────────────

async def authenticate_user(
    username: str,
    password: str,
    session: Session = Depends(get_session),
    use_ldap: bool = False,
) -> Optional[User]:
    """
    Authenticiert einen User via DB ODER LDAP.
    Gibt den User zurück, oder None bei Fehler.
    """
    # 1. User aus DB laden (muss existieren, auch bei LDAP)
    statement = select(User).where(User.username == username)
    user = session.exec(statement).first()
    
    if not user:
        return None
    
    # 2. Auth-Modus wählen
    if use_ldap and LDAP_ENABLED:
        return authenticate_ldap(user, password)
    else:
        return authenticate_db(user, password)


def authenticate_db(user: User, password: str) -> Optional[User]:
    """DB-Auth: bcrypt password hash"""
    if not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def authenticate_ldap(user: User, password: str) -> Optional[User]:
    """LDAP-Auth: Bind gegen LDAP-Server"""
    if not LDAP_ENABLED:
        return None
    try:
        import ldap
        conn = ldap.initialize(LDAP_SERVER)
        conn.protocol_version = ldap.VERSION3
        conn.set_option(ldap.OPT_REFERRALS, 0)
        
        # User-DN konstruieren
        user_search = LDAP_USER_SEARCH.format(username=user.username)
        dn = user.ldap_dn or f"uid={user.username},{LDAP_BASE_DN}"
        
        # Bind als User
        conn.simple_bind_s(dn, password)
        conn.unbind_s()
        return user
    except Exception:
        return None


# ─── RBAC Middleware / Dependencies ─────────────────────────────


async def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """
    Liest JWT aus Cookie, decodiert, lädt User aus DB.
    Wird als dependency in alle geschützten Routen verwendet.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht authentifiziert. Bitte einloggen.",
        )
    
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiges oder abgelaufenes Token.",
        )
    
    user_id: int = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Ungültiges Token.")
    
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User nicht gefunden.")
    
    return user


def require_global_admin():
    """
    Dependency-Factory: Prüft, ob der User global ADMIN ist.
    
    Usage:
        @router.get("/admin/stuff")
        async def admin_stuff(user: User = Depends(require_global_admin())):
    """
    async def role_check(
        request: Request,
        session: Session = Depends(get_session),
    ) -> User:
        # Manueler Auth-Check (get_current_user braucht Request)
        token = request.cookies.get("access_token")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Nicht authentifiziert.",
            )
        
        payload = decode_access_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ungültiges oder abgelaufenes Token.",
            )
        
        user_id: int = payload.get("sub")
        user = session.get(User, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User nicht gefunden.")
        
        if user.role != GlobalUserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Zugriff verweigert. Nur Administratoren.",
            )
        
        return user
    return role_check


_COURSE_ID_CACHE = {}


def require_course_access(*allowed_roles: CourseRole):
    """
    Dependency-Factory: Prüft Kurs-Zugehörigkeit + Rolle im Kurs.
    
    Die course_id wird automatisch vom URL-Path extrahiert (z.B. /courses/{course_id}/...).
    Kein manueller Parameter nötig!
    
    Global-Admin hat immer Zugriff auf alle Kurse.
    
    Usage:
        @router.get("/courses/{course_id}/tasks")
        async def list_tasks(
            course_id: int,
            session: Session = Depends(get_session),
            user: User = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
        ):
    """
    async def course_check(
        request: Request,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> tuple[User, int]:
        # course_id aus Path-Parametern extrahieren
        course_id = request.path_params.get("course_id")
        if course_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Keine course_id im Pfad gefunden.",
            )
        course_id = int(course_id)
        
        # Global-Admin hat überall Zugriff
        if user.role == GlobalUserRole.ADMIN:
            return user, course_id
        
        # Prüfen, ob User Mitglied des Kurses ist
        statement = (
            select(UserCourse)
            .where(UserCourse.user_id == user.id)
            .where(UserCourse.course_id == course_id)
        )
        membership = session.exec(statement).first()
        
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Du bist kein Mitglied dieses Kurses.",
            )
        
        if membership.role_in_course not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Zugriff verweigert. Deine Rolle im Kurs: {membership.role_in_course.value}",
            )
        
        return user, course_id
    return course_check