"""
TutorAI: FastAPI Application Entry Point

Start mit:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

oder:
    python -m uvicorn main:app --reload
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Sequence
import logging
import os
from pathlib import Path

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import hashlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from config import BASE_DIR, DEBUG, LLM_TIMEOUT
from database.base import create_db_and_tables, engine, get_session
from database.models import (
    Course,
    CourseInvite,
    CourseRole,
    FeedbackSource,
    GlobalUserRole,
    Task,
    User,
    UserCourse,
    Submission,
)
from services.auth_service import get_current_user, hash_password, require_course_access

from api import admin, auth, student, tutor, user_settings, course_members


def _calculate_percentile(my_score: float, other_scores: list[float]) -> int:
    if not other_scores:
        return 100
    below = sum(1 for s in other_scores if s < my_score)
    equal = sum(1 for s in other_scores if s == my_score)
    total = len(other_scores)
    return round((below + 0.5 * equal) / total * 100)


def _get_best_score(subs: Sequence[Submission]) -> float:
    if not subs:
        return 0.0
    latest_sub = subs[0]
    human_points = 0.0
    llm_points = 0.0
    override_exists = False
    for fb in latest_sub.feedback_list:
        if fb.source == FeedbackSource.HUMAN:
            human_points = max(human_points, fb.points_earned)
            override_exists = True
        else:
            llm_points = max(llm_points, fb.points_earned)
    return human_points if override_exists else llm_points


# ═══════════════════════════════════════════════════════════════════
# LIFESPAN: DB-Setup + Admin
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App-Start: DB-Tabellen erstellen + Admin-User bei leerer DB."""
    create_db_and_tables()

    # Admin-User anlegen, wenn DB leer
    with Session(engine) as session:
        admin_exists = session.exec(
            select(User).where(User.username == "admin")
        ).first()

        if not admin_exists:
            _create_admin(session)

    yield


def _create_admin(session: Session):
    """Erstellt einen Admin-User (admin / admin) für frische Installationen."""
    admin = User(
        username="admin",
        email="admin@tutor-system.local",
        name="Administrator",
        role=GlobalUserRole.ADMIN,
        password_hash=hash_password("admin"),
    )
    session.add(admin)
    session.commit()
    logger.info("Admin-User 'admin' erstellt (Passwort: admin). Bitte aendern!")


# ═══════════════════════════════════════════════════════════════════
# APP


app = FastAPI(
    title="TutorAI",
    description="AI-gestütztes Tutoring-System für Übungsaufgaben an Universitäten",
    version="0.1.0",
    lifespan=lifespan,
)

# Templates + Static
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ── Static asset caching (Tornado-style) ─────────────────────
# At startup, hash every file in static/ and build a lookup map.
# `asset("css/main.css")` → "/static/css/main.css?v=a1b2c3d4"
# Changing the file content changes the hash → browser fetches fresh.

_static_hashes: dict[str, str] = {}

if (BASE_DIR / "static").exists():
    for root, _dirs, files in os.walk(BASE_DIR / "static"):
        for fname in files:
            fpath = Path(root) / fname
            rel = fpath.relative_to(BASE_DIR / "static")
            h = hashlib.sha256(fpath.read_bytes()).hexdigest()[:8]
            _static_hashes[str(rel).replace("\\", "/")] = h

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _asset(path: str) -> str:
    """Tornado-style static_url: append content hash as ?v= query param."""
    key = path.lstrip("/")
    v = _static_hashes.get(key)
    if v:
        return f"/static/{key}?v={v}"
    return f"/static/{key}"


templates.env.globals["asset"] = _asset

# API-Routes
app.include_router(auth.router)
app.include_router(user_settings.router)
app.include_router(admin.router)
app.include_router(tutor.router)
app.include_router(course_members.router)
app.include_router(student.router)


# ═══════════════════════════════════════════════════════════════════
# GLOBAL EXCEPTION HANDLERS
# ═══════════════════════════════════════════════════════════════════

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """401 → Redirect zur Login-Seite statt JSON-Fehler."""
    if exc.status_code == 401:
        # HTMX-Requests: HX-Redirect Header (HTMX folgt automatisch)
        if "HX-Request" in request.headers:
            from fastapi.responses import Response
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse(url="/login", status_code=302)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# ═══════════════════════════════════════════════════════════════════
# WEB-PAGES (HTMX-gerendert)
# ═══════════════════════════════════════════════════════════════════

def _get_user_courses(user: User, session: Session) -> list[dict[str, Any]]:
    """Lädt alle Kurse eines Users mit Rolle."""
    memberships = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .order_by(UserCourse.id.desc())
    ).all()

    courses = []
    for m in memberships:
        course = session.get(Course, m.course_id)
        if course:
            courses.append({
                "id": course.id,
                "name": course.name,
                "semester": course.semester,
                "role_in_course": m.role_in_course.value,
            })
    return courses


@app.get("/")
async def index(
    request: Request,
    session: Session = Depends(get_session),
):
    """Home-Seite: Login oder Dashboard."""
    # Prüfen, ob User eingeloggt
    token = request.cookies.get("access_token")
    if not token:
        return templates.TemplateResponse("login.html", {"request": request})

    try:
        user = await get_current_user(request, session)
    except HTTPException:
        # Token ungültig → Login
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")

    courses = _get_user_courses(user, session)

    # Quick Stats fuer Studenten (nur wenn User mindestens eine STUDENT-Rolle hat)
    total_completed = 0
    total_points_earned = 0.0
    total_points_possible = 0
    stats_by_course = {}
    student_courses = [
        c for c in courses
        if c["role_in_course"] == CourseRole.STUDENT.value
    ]
    if student_courses:
        for c in student_courses:
            course_id = c["id"]
            tasks = session.exec(
                select(Task).where(Task.course_id == course_id)
            ).all()
            course_earned = 0.0
            course_possible = 0
            completed_count = 0
            for task in tasks:
                course_possible += task.max_points
                subs = session.exec(
                    select(Submission)
                    .where(Submission.task_id == task.id)
                    .where(Submission.student_id == user.id)
                    .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
                ).all()

                latest = 0.0
                if subs:
                    human_points = 0.0
                    llm_points = 0.0
                    override_exists = False
                    for fb in subs[0].feedback_list:
                        if fb.source == FeedbackSource.HUMAN:
                            human_points = max(human_points, fb.points_earned)
                            override_exists = True
                        else:
                            llm_points = max(llm_points, fb.points_earned)
                    latest = human_points if override_exists else llm_points

                if latest > 0:
                    completed_count += 1
                course_earned += latest
            total_completed += completed_count
            total_points_earned += course_earned
            total_points_possible += course_possible
            stats_by_course[course_id] = {
                "completed": completed_count,
                "total_tasks": len(tasks),
                "earned": course_earned,
                "possible": course_possible,
            }

    pct = 0.0
    if total_points_possible > 0:
        pct = round(total_points_earned / total_points_possible * 100, 1)

    # Rolle anzeigen: Admin bleibt "Admin", User zeigt die Rolle im aktuellen Kurs
    # Auf dem Dashboard (kein Kurs) zeigen wir die globale Rolle
    display_role = "Admin" if user.role == GlobalUserRole.ADMIN else "User"

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "page_title": "Dashboard",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": display_role,
            },
            "courses": courses,
            "selected_course_id": None,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "total_completed": total_completed,
            "total_points_earned": total_points_earned,
            "total_points_possible": total_points_possible,
            "total_percentage": pct,
            "stats_by_course": stats_by_course,
        },
    )


@app.get("/login")
async def login_page(request: Request):
    """Login-Seite anzeigen."""
    next_url = request.query_params.get("next", "")
    return templates.TemplateResponse("login.html", {"request": request, "next_url": next_url})


async def _do_login(
    username: str,
    password: str,
    session: Session,
    request: Request,
):
    """
    Gemeinsame Login-Logik (wird von POST /login und POST /api/auth/login genutzt).
    """
    username = username.strip()

    if not username or not password:
        return None, "Username und Password erforderlich.", 400

    from services.settings_resolver import get_effective_ldap_config
    from services.auth_service import authenticate_user, create_access_token

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
        return None, "Ungültiger Username oder Password.", 401

    token_data = {"sub": user.id, "username": user.username}
    token = create_access_token(token_data)

    return user, token, 200


@app.post("/login")
async def login_submit(
    request: Request,
    session: Session = Depends(get_session),
):
    """
    Login per Formular-POST (direkt an /login).

    Akzeptiert beide Content-Types:
    - application/x-www-form-urlencoded (normales HTML-Formular)
    - application/json (HTMX, Fetch-API, etc.)
    """
    content_type = request.headers.get("content-type", "")
    next_url = ""

    if "application/json" in content_type:
        # JSON-Body (HTMX / Fetch)
        body: dict = await request.json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        next_url = str(body.get("next_url", ""))
    else:
        # Form-encoded (normales HTML-Formular)
        form = await request.form()
        username_val = form.get("username", "")
        password_val = form.get("password", "")
        if hasattr(username_val, "read"):
            username = ""
        else:
            username = str(username_val)
        if hasattr(password_val, "read"):
            password = ""
        else:
            password = str(password_val)
        next_url = str(form.get("next_url", ""))

    user, token, status = await _do_login(username, password, session, request)

    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": token,  # Fehlermeldung
            "next_url": next_url,
        }, status_code=status)

    # Redirect zu next_url oder Default
    redirect_to = next_url if next_url else "/"
    # Sicherheitscheck: nur relative URLs erlauben (keine externen Redirects)
    if not redirect_to.startswith("/"):
        redirect_to = "/"

    response = RedirectResponse(url=redirect_to, status_code=303)
    response.set_cookie(
        key="access_token", value=token,
        httponly=not DEBUG, secure=not DEBUG, samesite="lax",
        max_age=8 * 3600, path="/",
    )
    return response


@app.get("/courses/{course_id}")
async def course_page(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Kurs-Übersicht: Aufgaben-Liste."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    # Rolle im Kurs
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Du bist kein Mitglied dieses Kurses.")

    # Tasks laden
    tasks = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).all()

    courses = _get_user_courses(user, session)

    # Template je nach Rolle
    is_tutor = membership.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)
    template = "tutor/course_overview.html" if is_tutor else "student/course_overview.html"

    # Für Studenten: nur sichtbare Aufgaben, my_points und has_feedback pro Aufgabe berechnen
    if not is_tutor:
        task_list = []
        for t in tasks:
            if not t.is_visible:
                continue
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == t.id)
                .where(Submission.student_id == user.id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
            ).all()

            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            has_feedback = False
            if subs:
                latest_sub = subs[0]  # newest
                for fb in latest_sub.feedback_list:
                    has_feedback = True
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)
            my_points = human_points if override_exists else llm_points

            task_list.append({
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type.value,
                "max_points": t.max_points,
                "max_attempts": t.max_attempts,
                "attempts_used": len(subs),
                "deadline": t.deadline,
                "has_tests": bool(t.test_code),
                "my_points": my_points,
                "has_feedback": has_feedback,
                "display_order": t.display_order,
            })
    else:
        task_list = [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type.value,
                "max_points": t.max_points,
                "max_attempts": t.max_attempts,
                "deadline": t.deadline,
                "has_tests": bool(t.test_code),
                "my_points": 0,
                "has_feedback": False,
                "is_visible": t.is_visible,
                "display_order": t.display_order,
            }
            for t in tasks
        ]

    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "page_title": course.name,
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": membership.role_in_course.value,
            },
            "courses": courses,
            "selected_course_id": course_id,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "course": {
                "id": course.id,
                "name": course.name,
                "description": course.description,
                "semester": course.semester,
            },
            "tasks": task_list,
            "is_tutor": is_tutor,
            "is_prof": membership.role_in_course == CourseRole.PROF,
        },
    )


@app.get("/courses/{course_id}/members")
async def members_page(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Mitglieder-Verwaltung: Für PROFs und Admins."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    is_admin = user.role == GlobalUserRole.ADMIN

    # Admin: darf alles — auch ohne Membership
    if not is_admin:
        membership = session.exec(
            select(UserCourse)
            .where(UserCourse.user_id == user.id)
            .where(UserCourse.course_id == course_id)
        ).first()

        if not membership or membership.role_in_course != CourseRole.PROF:
            raise HTTPException(403, "Nur PROFs und Administratoren können die Mitglieder verwalten.")
    else:
        # Für Admin ohne Membership: Dummy für die current_user-Display
        membership = None

    # Alle Mitglieder mit User-Info laden
    user_courses = session.exec(
        select(UserCourse).where(UserCourse.course_id == course_id)
    ).all()

    members = [
        {
            "id": uc.id,
            "user_id": uc.user_id,
            "username": uc.user.username if uc.user else "unknown",
            "name": uc.user.name if uc.user else "unknown",
            "role_in_course": uc.role_in_course.value,
        }
        for uc in user_courses
    ]

    courses = _get_user_courses(user, session)

    return templates.TemplateResponse(
        "tutor/members.html",
        {
            "request": request,
            "page_title": f"Mitglieder — {course.name}",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": membership.role_in_course.value if membership else "ADMIN",
            },
            "courses": courses,
            "selected_course_id": course_id,
            "is_admin": is_admin,
            "course": {
                "id": course.id,
                "name": course.name,
                "description": course.description,
                "semester": course.semester,
            },
            "members": members,
        },
    )


@app.get("/join/{token}")
async def join_page(
    token: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """
    Einladungsseite — Student tritt dem Kurs bei.

    Bei Klick auf den Einladungslink landet der Student hier.
    - Ist er bereits angemeldet → direkt beitreten.
    - Ist er nicht angemeldet → Redirect zum Login mit Next-URL.
    """
    invite = session.exec(
        select(CourseInvite).where(CourseInvite.token == token)
    ).first()

    if not invite:
        raise HTTPException(404, "Einladungslink ungültig oder nicht mehr vorhanden.")

    # Ablauf prüfen
    if invite.expires_at and invite.expires_at < datetime.now():
        raise HTTPException(410, "Dieser Einladungslink ist abgelaufen.")

    # Max-Nutzungen prüfen
    if invite.max_uses is not None and invite.used_count >= invite.max_uses:
        raise HTTPException(410, "Dieser Einladungslink wurde bereits maximal genutzt.")

    course = session.get(Course, invite.course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    user = None
    try:
        user = await get_current_user(request, session)
    except HTTPException:
        pass

    if not user:
        # Nicht eingeloggt → Redirect zum Login mit Next-URL
        next_url = f"/join/{token}"
        redirect = RedirectResponse(url=f"/login?next={next_url}", status_code=302)
        # Wir speichern eine kleine Message im Session-Flash (hier via Query-Param)
        return redirect

    assert user.id is not None, "User-ID ist None"
    user_id = user.id

    # Bereits im Kurs?
    existing = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user_id)
        .where(UserCourse.course_id == invite.course_id)
    ).first()

    if existing:
        return templates.TemplateResponse(
            "join.html",
            {
                "request": request,
                "page_title": f"Kurs beitreten — {course.name}",
                "current_user": {
                    "id": user_id,
                    "username": user.username,
                    "name": user.name,
                    "role": existing.role_in_course.value,
                },
                "courses": _get_user_courses(user, session),
                "selected_course_id": invite.course_id,
                "course": {
                    "id": course.id,
                    "name": course.name,
                    "description": course.description,
                    "semester": course.semester,
                },
                "already_member": True,
            },
        )

    # Student dem Kurs hinzufügen
    membership = UserCourse(
        user_id=user_id,
        course_id=invite.course_id,
        role_in_course=CourseRole.STUDENT,
    )
    session.add(membership)

    invite.used_count += 1
    session.add(invite)
    session.commit()

    return templates.TemplateResponse(
        "join.html",
        {
            "request": request,
            "page_title": f"Kurs beitreten — {course.name}",
            "current_user": {
                "id": user_id,
                "username": user.username,
                "name": user.name,
                "role": "STUDENT",
            },
            "courses": _get_user_courses(user, session),
            "selected_course_id": invite.course_id,
            "course": {
                "id": course.id,
                "name": course.name,
                "description": course.description,
                "semester": course.semester,
            },
            "already_member": False,
        },
    )


@app.get("/courses/{course_id}/tasks/new")
async def new_task_page(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Neue Aufgabe erstellen."""
    user, _ = user_and_course
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    courses = _get_user_courses(user, session)

    # Rolle im Kurs ermitteln
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    course_role = membership.role_in_course.value if membership else "TUTOR"

    # Global-Admin zeigt als PROF im Kurs-Kontext
    if user.role == GlobalUserRole.ADMIN:
        course_role = "PROF"

    return templates.TemplateResponse(
        "tutor/task_detail.html",
        {
            "request": request,
            "page_title": "Neue Aufgabe",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": course_role,
            },
            "courses": courses,
            "selected_course_id": course_id,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "course": {
                "id": course.id,
                "name": course.name,
            },
            "task": None,
            "is_tutor": True,
            "is_code": False,
            "code_editor": False,
            "LLM_TIMEOUT": LLM_TIMEOUT,
        },
    )


@app.get("/courses/{course_id}/tasks/{task_id}")
async def task_page(
    course_id: int,
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelne Aufgabe: Bearbeiten (Student) oder erstellen (Tutor)."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    if task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff.")

    is_tutor = membership.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)

    # Studenten duerfen versteckte Aufgaben nicht sehen
    if not is_tutor and not task.is_visible:
        raise HTTPException(404, "Aufgabe nicht gefunden oder noch nicht freigeschaltet.")

    is_code = task.task_type.value == "code"

    template = (
        "tutor/task_detail.html" if is_tutor
        else "student/task_solve.html"
    )

    courses = _get_user_courses(user, session)

    # Find previous and next task in the same course
    # Studenten: nur sichtbar e Aufgaben, TUTs/PROFs: alle Aufgaben
    prev_query = (
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.display_order < task.display_order)  # type: ignore[operator]
        .order_by(Task.display_order.desc())  # type: ignore[attr-defined]
    )
    if not is_tutor:
        prev_query = prev_query.where(Task.is_visible == True)  # type: ignore[operator]
    prev_task = session.exec(prev_query).first()

    next_query = (
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.display_order > task.display_order)  # type: ignore[operator]
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    )
    if not is_tutor:
        next_query = next_query.where(Task.is_visible == True)  # type: ignore[operator]
    next_task = session.exec(next_query).first()

    my_submissions = []
    latest_points = 0
    if not is_tutor:
        subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
            .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
        ).all()
        # Serialize zu sichere Dicts — keine rohen ORM-Objekte ans Template!
        my_submissions = []
        for sub in subs:
            feedbacks = []
            for fb in sub.feedback_list:
                feedbacks.append({
                    "id": fb.id,
                    "source": fb.source.value,
                    "points_earned": fb.points_earned,
                    "comment": fb.comment,
                    "giver_name": fb.giver.name if fb.giver else "LLM",
                    "created_at": fb.created_at,
                })
            my_submissions.append({
                "id": sub.id,
                "solution": sub.solution,
                "code_solution": sub.code_solution,
                "attempt_number": sub.attempt_number,
                "submitted_at": sub.submitted_at,
                "status": sub.status.value,
                "feedback_list": feedbacks,
            })
        if my_submissions:
            latest_sub = my_submissions[0]  # newest (ordered desc)
            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            for fb in latest_sub["feedback_list"]:
                if fb["source"] == "human":
                    human_points = max(human_points, fb["points_earned"])
                    override_exists = True
                else:
                    llm_points = max(llm_points, fb["points_earned"])
            latest_points = human_points if override_exists else llm_points

        # Perzentil fuer aktuelle Aufgabe berechnen
        student_members = session.exec(
            select(UserCourse)
            .where(UserCourse.course_id == course_id)
            .where(UserCourse.role_in_course == CourseRole.STUDENT)
        ).all()
        other_scores = []
        for member in student_members:
            if member.user_id == user.id:
                continue
            other_subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == member.user_id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attrdefined]
            ).all()
            other_scores.append(_get_best_score(other_subs))
        task_percentile = _calculate_percentile(latest_points, other_scores)

        # Gruppen-Durchschnitt: Alle Kursstudenten zählen (ohne Abgabe = 0 Punkte)
        all_scores = other_scores + [latest_points]
        task_group_avg = round(sum(all_scores) / len(all_scores), 1)
    else:
        task_percentile = None
        task_group_avg = None

    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "page_title": task.title,
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": membership.role_in_course.value,
            },
            "courses": courses,
            "selected_course_id": course_id,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "course": {
                "id": course.id,
                "name": course.name,
            },
            "task": {
                "id": task.id,
                "title": task.title,
                "task_type": task.task_type.value,
                "description": task.description,
                "max_points": task.max_points,
                "max_attempts": task.max_attempts,
                "deadline": task.deadline,
                "code_template": task.code_template,
                "model_solution": task.model_solution if is_tutor else None,
                "test_code": task.test_code if is_tutor else None,
                "is_visible": task.is_visible if is_tutor else None,
            },
            "is_tutor": is_tutor,
            "is_code": is_code,
            "code_editor": is_code,
            "my_submissions": my_submissions,
            "latest_points": latest_points,
            "total_attempts": len(my_submissions),
            "task_percentile": task_percentile,
            "task_group_avg": task_group_avg,

            "LLM_TIMEOUT": LLM_TIMEOUT,
            "prev_task": {"id": prev_task.id, "title": prev_task.title} if prev_task else None,
            "next_task": {"id": next_task.id, "title": next_task.title} if next_task else None,
        },
    )


@app.get("/courses/{course_id}/overview")
async def overview_page(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Tutor: Übersichtstabelle mit Excel-Export."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Nur für PROF/Tutor.")

    courses = _get_user_courses(user, session)

    return templates.TemplateResponse(
        "tutor/overview.html",
        {
            "request": request,
            "page_title": f"Übersicht — {course.name}",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": membership.role_in_course.value,
            },
            "courses": courses,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "selected_course_id": course_id,
            "course": {
                "id": course.id,
                "name": course.name,
            },
        },
    )


@app.get("/courses/{course_id}/tasks/{task_id}/students/{student_id}/review")
async def submission_review_page(
    course_id: int,
    task_id: int,
    student_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Tutor: Bewertungsseite für eine Studenteneinreichung."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Nur für PROF/Tutor.")

    task = session.get(Task, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    student = session.get(User, student_id)
    if not student:
        raise HTTPException(404, "Student nicht gefunden.")

    courses = _get_user_courses(user, session)

    # Previous and next visible tasks (like student view)
    prev_task_obj = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.display_order < task.display_order)  # type: ignore[operator]
        .where(Task.is_visible == True)
        .order_by(Task.display_order.desc())  # type: ignore[attr-defined]
    ).first()

    next_task_obj = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.display_order > task.display_order)  # type: ignore[operator]
        .where(Task.is_visible == True)
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).first()

    # Calculate student's latest points for this task (like student view)
    student_subs = session.exec(
        select(Submission)
        .where(Submission.task_id == task_id)
        .where(Submission.student_id == student_id)
        .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
    ).all()
    latest_points = 0.0
    if student_subs:
        latest_sub = student_subs[0]  # newest
        human_points = 0.0
        llm_points = 0.0
        override_exists = False
        for fb in latest_sub.feedback_list:
            if fb.source == FeedbackSource.HUMAN:
                human_points = max(human_points, fb.points_earned)
                override_exists = True
            elif fb.source == FeedbackSource.LLM:
                llm_points = max(llm_points, fb.points_earned)
        latest_points = human_points if override_exists else llm_points

    task_type_display = {"text": "Textaufgabe", "code": "Codeaufgabe"}.get(
        task.task_type.value, task.task_type.value
    )

    return templates.TemplateResponse(
        "tutor/submission_review.html",
        {
            "request": request,
            "page_title": f"Bewertung — {task.title}",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": membership.role_in_course.value,
            },
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "courses": courses,
            "selected_course_id": course_id,
            "course_id": course_id,
            "task_id": task_id,
            "student_id": student_id,
            "task_title": task.title,
            "task_type_display": task_type_display,
            "task_type": task.task_type.value,
            "max_points": task.max_points,
            "student_name": student.name,
            "student_username": student.username,
            "course": {
                "id": course.id,
                "name": course.name,
            },
            "latest_points": latest_points,
            "total_attempts": len(student_subs),
            "prev_task": {"id": prev_task_obj.id, "title": prev_task_obj.title} if prev_task_obj else None,
            "next_task": {"id": next_task_obj.id, "title": next_task_obj.title} if next_task_obj else None,
        },
    )


@app.get("/settings")
async def settings_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """User-Einstellungen: Name / Passwort ändern."""
    courses = _get_user_courses(user, session)
    use_ldap = not user.password_hash  # LDAP-User haben keinen DB-Password-Hash

    # Rolle anzeigen: Admin bleibt "Admin", User zeigt generisch "User"
    display_role = "Admin" if user.role == GlobalUserRole.ADMIN else "User"

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "page_title": "Einstellungen",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": display_role,
            },
            "courses": courses,
            "selected_course_id": None,
            "is_admin": user.role == GlobalUserRole.ADMIN,
            "use_ldap": use_ldap,
        },
    )


@app.get("/admin")
async def admin_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Admin: Kurse verwalten, User erstellen, Settings."""
    # Admin-Check (manuell, da wir in Page-Route sind)
    if user.role != GlobalUserRole.ADMIN:
        raise HTTPException(403, "Nur für Administratoren.")

    # Alle Kurse für den Überblick
    all_courses = session.exec(select(Course)).all()

    # Memberships des Users sammeln (für Link-Logik)
    user_memberships = session.exec(
        select(UserCourse).where(UserCourse.user_id == user.id)
    ).all()
    member_course_ids = {m.course_id for m in user_memberships}
    membership_roles = {m.course_id: m.role_in_course.value for m in user_memberships}

    courses = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "semester": c.semester,
            "role_in_course": membership_roles.get(c.id, "NONE"),
            "is_member": c.id in member_course_ids,
        }
        for c in all_courses
    ]

    all_users = session.exec(select(User)).all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "page_title": "Admin",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": user.role.value,
            },
            "courses": courses,
            "selected_course_id": None,
            "is_admin": True,
            "all_users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "name": u.name,
                    "email": u.email,
                    "role": u.role.value,
                }
                for u in all_users
            ],
        },
    )
