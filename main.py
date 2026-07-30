"""
Tutor-System: FastAPI Application Entry Point

Start mit:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

oder:
    python -m uvicorn main:app --reload
"""

from contextlib import asynccontextmanager
from typing import Any
import logging

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from config import BASE_DIR, LLM_TIMEOUT
from database.base import create_db_and_tables, engine, get_session
from database.models import (
    Course,
    CourseRole,
    FeedbackSource,
    GlobalUserRole,
    Task,
    TaskType,
    TestCase,
    TestVisibility,
    User,
    UserCourse,
    Submission,
)
from services.auth_service import get_current_user, hash_password, require_course_access

from api import admin, auth, student, tutor, user_settings, course_members


# ═══════════════════════════════════════════════════════════════════
# LIFESPAN: DB-Setup + Seed-Data
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App-Start: DB-Tabellen + Migration + Demo-Daten erstellen."""
    # 1. Tabellen erstellen
    create_db_and_tables()
    
    # 2. Migration: Alte UserRole-Werte in neue Enums umwandeln
    _migrate_roles()
    
    # 2b. Migration: ldap_bind_pw Spalte zu course_settings hinzufuegen
    _migrate_course_settings_bind_pw()
    
    # 2c. Migration: ldap_user_search Spalte zu course_settings hinzufuegen
    _migrate_course_settings_search_filter()
    
    # 3. Seed-Daten (wenn DB leer)
    with Session(engine) as session:
        # Einfacher Check: Admin existieren?
        admin_exists = session.exec(
            select(User).where(User.username == "admin")
        ).first()
        
        if not admin_exists:
            _seed_demo_data(session)
    
    yield


def _migrate_roles():
    """
    Migration von altem UserRole (ADMIN/TUTOR/STUDENT) zu neuem Schema.
    
    - User.role: "ADMIN" → GlobalUserRole.ADMIN, "TUTOR"/"STUDENT" → GlobalUserRole.USER
    - UserCourse.role_in_course: "ADMIN" → CourseRole.PROF, "TUTOR"/"STUDENT" bleibt
    """
    from sqlalchemy import text
    
    with Session(engine) as session:
        try:
            # 1. User.role migrieren
            session.execute(text(
                "UPDATE users SET role = 'USER' WHERE role IN ('TUTOR', 'STUDENT')"
            ))
            # ADMIN bleibt ADMIN
            
            # 2. UserCourse.role_in_course migrieren
            session.execute(text(
                "UPDATE user_courses SET role_in_course = 'PROF' WHERE role_in_course = 'ADMIN'"
            ))
            # TUTOR und STUDENT bleiben unverändert
            
            session.commit()
            print("Rollen-Migration abgeschlossen.")
        except Exception as e:
            # Wenn die Tabelle noch nicht existiert oder Migration bereits gelaufen ist
            print(f"Rollen-Migration uebersprungen: {e}")
            session.rollback()


def _migrate_course_settings_bind_pw():
    """
    Fügt die ldap_bind_pw-Spalte zur course_settings-Tabelle hinzu,
    falls sie noch nicht existiert (SQLite-Erweiterung).
    """
    from sqlalchemy import text

    with Session(engine) as session:
        try:
            session.execute(text(
                "ALTER TABLE course_settings ADD COLUMN ldap_bind_pw TEXT DEFAULT NULL"
            ))
            session.commit()
            print("Migration: ldap_bind_pw zu course_settings hinzugefuegt.")
        except Exception as e:
            # Spalte existiert bereits oder Tabelle noch nicht da
            err_str = str(e).lower()
            if "duplicate" in err_str or "already exists" in err_str or "no such table" in err_str:
                print("Migration ldap_bind_pw: Bereits vorhanden.")
            else:
                print(f"Migration ldap_bind_pw fehlgeschlagen: {e}")
                session.rollback()


def _migrate_course_settings_search_filter():
    """
    Fügt die ldap_user_search-Spalte zur course_settings-Tabelle hinzu.
    """
    from sqlalchemy import text

    with Session(engine) as session:
        try:
            session.execute(text(
                "ALTER TABLE course_settings ADD COLUMN ldap_user_search TEXT DEFAULT NULL"
            ))
            session.commit()
            print("Migration: ldap_user_search zu course_settings hinzugefuegt.")
        except Exception as e:
            err_str = str(e).lower()
            if "duplicate" in err_str or "already exists" in err_str or "no such table" in err_str:
                print("Migration ldap_user_search: Bereits vorhanden.")
            else:
                print(f"Migration ldap_user_search fehlgeschlagen: {e}")
                session.rollback()


def _seed_demo_data(session: Session):
    """Erstellt Demo-User, Kurs, und Beispiel-Aufgabe."""
    
    print("Demo-Daten werden erstellt...")
    
    # ─── Admin ──────────────────────────────────────────────────
    admin = User(
        username="admin",
        email="admin@uni.de",
        name="Prof. Admin",
        role=GlobalUserRole.ADMIN,
        password_hash=hash_password("admin123"),
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    
    # ─── Tutor ──────────────────────────────────────────────────
    tutor_user = User(
        username="tutor1",
        email="tutor@uni.de",
        name="Dr. Tutor",
        role=GlobalUserRole.USER,
        password_hash=hash_password("tutor123"),
    )
    session.add(tutor_user)
    session.commit()
    session.refresh(tutor_user)
    
    # ─── Students ───────────────────────────────────────────────
    for i in range(1, 6):
        stu = User(
            username=f"student{i}",
            email=f"student{i}@uni.de",
            name=f"Student {i}",
            role=GlobalUserRole.USER,
            password_hash=hash_password("student123"),
        )
        session.add(stu)
    session.commit()
    
    # Typing-Hilfe: session.refresh() garantiert, dass .id nun int ist (nicht None),
    # aber mypy erkennt das nicht. Wir casten daher explizit.
    admin_id: int = admin.id  # type: ignore[assignment]
    tutor_id: int = tutor_user.id  # type: ignore[assignment]
    
    # ─── Kurs ───────────────────────────────────────────────────
    course = Course(
        name="Einführung in die Informatik",
        description="Grundlagen der Programmierung und Algorithmen",
        semester="WS 2025/26",
        created_by=admin_id,
    )
    session.add(course)
    session.commit()
    session.refresh(course)
    course_id: int = course.id  # type: ignore[assignment]
    
    # ─── Kurs-Mitglieder ────────────────────────────────────────
    # Admin → Kurs (als PROF)
    session.add(UserCourse(
        user_id=admin_id, course_id=course_id, role_in_course=CourseRole.PROF
    ))
    
    # Tutor → Kurs
    session.add(UserCourse(
        user_id=tutor_id, course_id=course_id, role_in_course=CourseRole.TUTOR
    ))
    
    # Students → Kurs
    for i in range(1, 6):
        stu = session.exec(
            select(User).where(User.username == f"student{i}")
        ).first()
        if stu:
            session.add(UserCourse(
                user_id=stu.id,  # type: ignore[arg-type]
                course_id=course_id,
                role_in_course=CourseRole.STUDENT,
            ))
    
    # ─── Beispiel-Aufgabe (Text) ────────────────────────────────
    task = Task(
        course_id=course_id,
        created_by=tutor_id,
        title="Blatt1-01: Variablen und Datentypen",
        task_type=TaskType.TEXT,
        description=(
            "Erklären Sie den Unterschied zwischen einer Variable und einem Konstanten Wert in Python.\n\n"
            "Geben Sie für jeden der folgenden Datentypen ein Beispiel:\n"
            "- Integer\n"
            "- Float\n"
            "- String\n"
            "- Boolean\n\n"
            "Wie würde man in Python den Typ einer Variable ermitteln?"
        ),
        model_solution=(
            "Eine Variable ist ein benannter Speicherplatz, dessen Wert sich ändern kann. "
            "Eine Konstante ist ein Wert, der sich nicht ändert (in Python durch Großschreibung gekennzeichnet).\n\n"
            "Beispiele:\n"
            "- Integer: 42, -7, 0\n"
            "- Float: 3.14, -0.5, 2.0\n"
            "- String: \"Hallo\", 'Welt', \"123\"\n"
            "- Boolean: True, False\n\n"
            "Den Typ einer Variable ermittelt man mit type(variable)."
        ),
        max_points=10,
        max_attempts=3,
        deadline="2025-02-15T23:59",
    )
    session.add(task)
    session.commit()
    
    # ─── Beispiel-Aufgabe (Code) ────────────────────────────────
    code_task = Task(
        course_id=course_id,
        created_by=tutor_id,
        title="Blatt1-02: Fakultaet berechnen",
        task_type=TaskType.CODE,
        description=(
            "Implementieren Sie eine Funktion `fakultaet(n)`, die die Fakultät einer nicht-negativen Ganzzahl berechnet.\n\n"
            "Die Fakultät n! ist definiert als: n! = n × (n-1) × ... × 2 × 1\n"
            "Außerdem gilt: 0! = 1\n\n"
            "Verwenden Sie Rekursion für die Implementierung."
        ),
        model_solution=(
            "def fakultaet(n):\n"
            "    if n <= 1:\n"
            "        return 1\n"
            "    return n * fakultaet(n - 1)"
        ),
        max_points=15,
        max_attempts=5,
        deadline="2025-02-15T23:59",
        code_template=(
            "def fakultaet(n):\n"
            "    # TODO: Implementieren Sie hier die rekursive Fakultät\n"
            "    pass\n"
        ),
    )
    session.add(code_task)
    session.commit()
    session.refresh(code_task)
    
    code_task_id: int = code_task.id  # type: ignore[assignment]
    
    # Test-Cases für die Code-Aufgabe
    test_cases = [
        TestCase(
            task_id=code_task_id,
            name="test_0",
            code=(
                "import unittest\n"
                "class TestFakultaet(unittest.TestCase):\n"
                "    def test_fak_0(self):\n"
                "        self.assertEqual(fakultaet(0), 1)"
            ),
            expected_output="OK",
            visibility=TestVisibility.PUBLIC,
        ),
        TestCase(
            task_id=code_task_id,
            name="test_1",
            code=(
                "import unittest\n"
                "class TestFakultaet(unittest.TestCase):\n"
                "    def test_fak_1(self):\n"
                "        self.assertEqual(fakultaet(1), 1)"
            ),
            expected_output="OK",
            visibility=TestVisibility.PUBLIC,
        ),
        TestCase(
            task_id=code_task_id,
            name="test_5",
            code=(
                "import unittest\n"
                "class TestFakultaet(unittest.TestCase):\n"
                "    def test_fak_5(self):\n"
                "        self.assertEqual(fakultaet(5), 120)"
            ),
            expected_output="OK",
            visibility=TestVisibility.PUBLIC,
        ),
        TestCase(
            task_id=code_task_id,
            name="test_10",
            code=(
                "import unittest\n"
                "class TestFakultaet(unittest.TestCase):\n"
                "    def test_fak_10(self):\n"
                "        self.assertEqual(fakultaet(10), 3628800)"
            ),
            expected_output="OK",
            visibility=TestVisibility.PRIVATE,
        ),
        TestCase(
            task_id=code_task_id,
            name="test_negative",
            code=(
                "import unittest\n"
                "class TestFakultaet(unittest.TestCase):\n"
                "    def test_negative_input(self):\n"
                "        self.assertRaises((ValueError, TypeError), fakultaet, -1)"
            ),
            expected_output="OK",
            visibility=TestVisibility.PRIVATE,
        ),
    ]
    for tc in test_cases:
        session.add(tc)
    session.commit()
    
    print("Demo-Daten erstellt:")
    print("   - 1 Admin (admin / admin123)")
    print("   - 1 Tutor (tutor1 / tutor123)")
    print("   - 5 Students (student1-5 / student123)")
    print("   - 1 Kurs: 'Einführung in die Informatik'")
    print("   - 2 Aufgaben (1 Text, 1 Code mit 5 Tests)")


# ═══════════════════════════════════════════════════════════════════
# APP

app = FastAPI(
    title="Tutor-System",
    description="AI-gestütztes Tutor-System für Übungsaufgaben an Universitäten",
    version="0.1.0",
    lifespan=lifespan,
)

# Templates + Static
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Static-Files (CSS, JS)
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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
    ).all()
    
    courses = []
    for m in memberships:
        course = session.get(Course, m.course_id)
        if course:
            courses.append({
                "id": course.id,
                "name": course.name,
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
                ).all()
                best = 0.0
                for sub in subs:
                    for fb in sub.feedback_list:
                        if fb.points_earned > best:
                            best = fb.points_earned
                if best > 0:
                    completed_count += 1
                course_earned += best
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
    return templates.TemplateResponse("login.html", {"request": request})


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
    
    from database.models import CourseSettings
    from services.auth_service import authenticate_user, create_access_token
    
    # LDAP-Setting aus CourseSettings lesen
    first_settings = session.exec(
        select(CourseSettings).where(CourseSettings.use_ldap == True)
    ).first()
    
    use_ldap = False
    ldap_server = None
    ldap_base_dn = None
    ldap_bind_dn = None
    ldap_bind_pw = None
    ldap_user_search = None
    
    if first_settings:
        use_ldap = True
        ldap_server = first_settings.ldap_server
        ldap_base_dn = first_settings.ldap_base_dn
        ldap_bind_dn = first_settings.ldap_bind_dn
        ldap_bind_pw = first_settings.ldap_bind_pw
        ldap_user_search = first_settings.ldap_user_search
    
    user = await authenticate_user(
        username, password, session, use_ldap,
        ldap_server=ldap_server,
        ldap_base_dn=ldap_base_dn,
        ldap_bind_dn=ldap_bind_dn,
        ldap_bind_pw=ldap_bind_pw,
        ldap_user_search=ldap_user_search,
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
    
    if "application/json" in content_type:
        # JSON-Body (HTMX / Fetch)
        body: dict = await request.json()
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
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
    
    user, token, status = await _do_login(username, password, session, request)
    
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": token,  # Fehlermeldung
        }, status_code=status)
    
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="access_token", value=token,
        httponly=False, secure=False, samesite="lax",
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
        .order_by(Task.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    
    courses = _get_user_courses(user, session)
    
    # Template je nach Rolle
    is_tutor = membership.role_in_course in (CourseRole.PROF, CourseRole.TUTOR)
    template = "tutor/course_overview.html" if is_tutor else "student/course_overview.html"
    
    # Für Studenten: my_points und has_feedback pro Aufgabe berechnen
    if not is_tutor:
        task_list = []
        for t in tasks:
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == t.id)
                .where(Submission.student_id == user.id)
            ).all()
            
            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            has_feedback = False
            for sub in subs:
                for fb in sub.feedback_list:
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
                "test_count": len(t.test_cases),
                "my_points": my_points,
                "has_feedback": has_feedback,
            })
    else:
        task_list = [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type.value,
                "max_points": t.max_points,
                "test_count": len(t.test_cases),
                "my_points": 0,
                "has_feedback": False,
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
    is_code = task.task_type.value == "code"
    
    template = (
        "tutor/task_detail.html" if is_tutor
        else "student/task_solve.html"
    )
    
    courses = _get_user_courses(user, session)
    
    # Student: eigene Submissions laden
    # Find previous and next task in the same course
    prev_task = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.id < task_id)  # type: ignore[operator]
        .order_by(Task.id.desc())  # type: ignore[attr-defined]
    ).first()

    next_task = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.id > task_id)  # type: ignore[operator]
        .order_by(Task.id.asc())  # type: ignore[attr-defined]
    ).first()

    my_submissions = []
    best_points = 0
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
                if fb.points_earned > best_points:
                    best_points = fb.points_earned
            my_submissions.append({
                "id": sub.id,
                "solution": sub.solution,
                "code_solution": sub.code_solution,
                "attempt_number": sub.attempt_number,
                "submitted_at": sub.submitted_at,
                "status": sub.status.value,
                "feedback_list": feedbacks,
            })
    
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
                "test_cases": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "code": tc.code,
                        "visibility": str(tc.visibility),
                    }
                    for tc in task.test_cases
                ] if is_tutor else [],
            },
            "is_tutor": is_tutor,
            "is_code": is_code,
            "code_editor": is_code,
            "my_submissions": my_submissions,
            "best_points": best_points,
            "total_attempts": len(my_submissions),
            "public_tests": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "code": tc.code,
                    "visibility": str(tc.visibility),
                }
                for tc in task.test_cases
                if str(tc.visibility) == "public"
            ],
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

