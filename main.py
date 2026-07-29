"""
Tutor-System: FastAPI Application Entry Point

Start mit:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

oder:
    python -m uvicorn main:app --reload
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from config import BASE_DIR, LLM_TIMEOUT
from database.base import create_db_and_tables, engine, get_session
from database.models import (
    Course,

    FeedbackSource,
    Task,
    TaskType,
    TestCase,
    TestVisibility,
    User,
    UserCourse,
    UserRole,
    Submission,
)
from services.auth_service import get_current_user, hash_password, require_course_access

from api import admin, auth, student, tutor, user_settings


# ═══════════════════════════════════════════════════════════════════
# LIFESPAN: DB-Setup + Seed-Data
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App-Start: DB-Tabellen + Demo-Daten erstellen."""
    # 1. Tabellen erstellen
    create_db_and_tables()
    
    # 2. Seed-Daten (wenn DB leer)
    with Session(engine) as session:
        # Einfacher Check: Admin existieren?
        admin_exists = session.exec(
            select(User).where(User.username == "admin")
        ).first()
        
        if not admin_exists:
            _seed_demo_data(session)
    
    yield


def _seed_demo_data(session: Session):
    """Erstellt Demo-User, Kurs, und Beispiel-Aufgabe."""
    
    print("Demo-Daten werden erstellt...")
    
    # ─── Admin ──────────────────────────────────────────────────
    admin = User(
        username="admin",
        email="admin@uni.de",
        name="Prof. Admin",
        role=UserRole.ADMIN,
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
        role=UserRole.TUTOR,
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
            role=UserRole.STUDENT,
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
    # Admin → Kurs
    session.add(UserCourse(
        user_id=admin_id, course_id=course_id, role_in_course=UserRole.ADMIN
    ))
    
    # Tutor → Kurs
    session.add(UserCourse(
        user_id=tutor_id, course_id=course_id, role_in_course=UserRole.TUTOR
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
                role_in_course=UserRole.STUDENT,
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

    # Quick Stats fuer Studenten
    total_completed = 0
    total_points_earned = 0.0
    total_points_possible = 0
    stats_by_course = {}
    if user.role.value == "student":
        for c in courses:
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

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "page_title": "Dashboard",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": user.role.value,
            },
            "courses": courses,
            "selected_course_id": None,
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
    
    from config import LDAP_ENABLED
    from services.auth_service import authenticate_user, create_access_token
    
    user = await authenticate_user(username, password, session, LDAP_ENABLED)
    
    if not user:
        return None, "Ungültiger Username oder Password.", 401
    
    token_data = {"sub": user.id, "username": user.username, "role": user.role.value}
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
    is_tutor = membership.role_in_course in (UserRole.ADMIN, UserRole.TUTOR)
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
            "course": {
                "id": course.id,
                "name": course.name,
                "description": course.description,
                "semester": course.semester,
            },
            "tasks": task_list,
            "is_tutor": is_tutor,
        },
    )


@app.get("/courses/{course_id}/tasks/new")
async def new_task_page(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(UserRole.ADMIN, UserRole.TUTOR)),
):
    """Neue Aufgabe erstellen."""
    user, _ = user_and_course
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    courses = _get_user_courses(user, session)

    return templates.TemplateResponse(
        "tutor/task_detail.html",
        {
            "request": request,
            "page_title": "Neue Aufgabe",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": "tutor",
            },
            "courses": courses,
            "selected_course_id": course_id,
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
    
    is_tutor = membership.role_in_course in (UserRole.ADMIN, UserRole.TUTOR)
    is_code = task.task_type.value == "code"
    
    template = (
        "tutor/task_detail.html" if is_tutor
        else "student/task_solve.html"
    )
    
    courses = _get_user_courses(user, session)
    
    # Student: eigene Submissions laden
    my_submissions = []
    best_points = 0
    if not is_tutor:
        subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
            .order_by(Submission.submitted_at.desc())
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
    
    if not membership or membership.role_in_course not in (UserRole.ADMIN, UserRole.TUTOR):
        raise HTTPException(403, "Nur für Tutor/Admin.")
    
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
    
    if not membership or membership.role_in_course not in (UserRole.ADMIN, UserRole.TUTOR):
        raise HTTPException(403, "Nur für Tutor/Admin.")
    
    task = session.get(Task, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    student = session.get(User, student_id)
    if not student:
        raise HTTPException(404, "Student nicht gefunden.")
    
    courses = _get_user_courses(user, session)
    
    task_type_display = {"text": "Textaufgabe", "code": "Codeaufgabe", "mc": "Multiple Choice"}.get(
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
            "courses": courses,
            "selected_course_id": course_id,
            "course_id": course_id,
            "task_id": task_id,
            "student_id": student_id,
            "task_title": task.title,
            "task_type_display": task_type_display,
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

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "page_title": "Einstellungen",
            "current_user": {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "role": user.role.value,
            },
            "courses": courses,
            "selected_course_id": None,
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
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Nur für Administratoren.")
    
    courses = _get_user_courses(user, session)
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

