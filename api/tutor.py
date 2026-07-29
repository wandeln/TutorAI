"""
Tutor-Endpoints: Aufgaben-Management + Korrektur + Übersicht.

Rollen: Tutor und PROF (im Kurs), Admin (global)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    Course,
    CourseRole,
    Feedback,
    FeedbackSource,
    GlobalUserRole,
    Submission,
    SubmissionStatus,
    Task,
    TaskType,
    TestCase,
    TestCaseCreate,
    User,
    UserCourse,
)
from services.auth_service import get_current_user, require_course_access
from services.export_service import ExportService
from services.grading_service import GradingService
from services.llm_service import LLMService

router = APIRouter(prefix="/api", tags=["Tutor"])
grading_service = GradingService()
llm_service = LLMService()
export_service = ExportService()


def _check_course_role(user: User, course_id: int, session: Session):
    """
    Prüft, ob der User im Kurs PROF oder TUTOR ist.
    Global-Admin hat immer Zugriff.
    Hebt HTTPException 403, falls keine Berechtigung.
    """
    if user.role == GlobalUserRole.ADMIN:
        return
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Nur PROF/Tutor dürfen auf diese Daten zugreifen.")


# ═══════════════════════════════════════════════════════════════════
# AUFGABEN
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/tasks")
async def list_tasks(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Alle Aufgaben eines Kurses auflisten."""
    tasks = list(session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .order_by(Task.created_at.desc())  # type: ignore[attr-defined]
    ).all())
    
    return [
        {
            "id": t.id,
            "title": t.title,
            "task_type": t.task_type.value,
            "max_points": t.max_points,
            "max_attempts": t.max_attempts,
            "deadline": t.deadline,
            "test_count": len(t.test_cases),
            "submission_count": len(t.submissions),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.post("/courses/{course_id}/tasks")
async def create_task(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Neue Aufgabe erstellen."""
    user, _ = user_and_course
    body = await request.json()
    
    model_solution = body.get("model_solution")
    # model_solution kann None sein (optional)
    if model_solution == "":
        model_solution = None
    
    task = Task(
        course_id=course_id,
        created_by=user.id,  # type: ignore[arg-type]
        title=body.get("title", ""),
        task_type=TaskType(body.get("task_type", "text")),
        description=body.get("description", ""),
        model_solution=model_solution,
        max_points=body.get("max_points", 10),
        max_attempts=body.get("max_attempts"),
        deadline=body.get("deadline"),
        code_template=body.get("code_template"),
    )
    
    session.add(task)
    session.commit()
    session.refresh(task)
    
    task_id: int = task.id  # type: ignore[assignment]
    # Test-Cases erstellen
    for tc_data in body.get("test_cases", []):
        tc = TestCase(
            task_id=task_id,
            name=tc_data.get("name", ""),
            code=tc_data.get("code", ""),
            expected_output=tc_data.get("expected_output", ""),
            visibility=tc_data.get("visibility", "public"),
            input_data=tc_data.get("input_data"),
        )
        session.add(tc)
    
    session.commit()
    session.refresh(task)
    
    return {
        "message": f"Aufgabe '{task.title}' erstellt.",
        "task": {
            "id": task.id,
            "title": task.title,
            "task_type": task.task_type.value,
        },
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelne Aufgabe laden (mit Test-Cases)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    # Check course access
    statement = (
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    )
    if not session.exec(statement).first():
        raise HTTPException(403, "Kein Zugriff auf diese Aufgabe.")
    
    is_tutor = (
        user.role == GlobalUserRole.ADMIN
        or (
            session.exec(
                select(UserCourse.role_in_course)
                .where(UserCourse.user_id == user.id)
                .where(UserCourse.course_id == task.course_id)
            ).first()
            in (CourseRole.PROF, CourseRole.TUTOR)
        )
    )
    
    # Studenten sehen keine Musterlösung und keine privaten Tests
    result = {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type.value,
        "description": task.description,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "deadline": task.deadline,
        "code_template": task.code_template if task.task_type.value == "code" else None,
        "test_cases": [],
    }
    
    if is_tutor:
        result["model_solution"] = task.model_solution
        result["test_cases"] = [
            {
                "id": tc.id,
                "name": tc.name,
                "code": tc.code,
                "visibility": str(tc.visibility),
            }
            for tc in task.test_cases
        ]
    else:
        # Nur public Tests
        result["test_cases"] = [
            {
                "id": tc.id,
                "name": tc.name,
                "code": tc.code if str(tc.visibility) == "public" else "***",
                "visibility": str(tc.visibility),
            }
            for tc in task.test_cases
        ]
    
    return result


@router.put("/tasks/{task_id}")
async def update_task(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aufgabe bearbeiten (inkl. Test-Cases)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    # Manueler Kurs-Zugriffs-Check (course_id nicht im Path)
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung, diese Aufgabe zu bearbeiten.")
    
    body = await request.json()
    
    # Update allowed fields
    if "title" in body: task.title = body["title"]
    if "task_type" in body: task.task_type = TaskType(body["task_type"])
    if "description" in body: task.description = body["description"]
    
    # model_solution: leerer String → None (optional)
    if "model_solution" in body:
        val = body["model_solution"]
        task.model_solution = None if val == "" else val
    
    if "max_points" in body: task.max_points = body["max_points"]
    if "max_attempts" in body: task.max_attempts = body["max_attempts"]
    if "deadline" in body: task.deadline = body["deadline"]
    if "code_template" in body: task.code_template = body["code_template"]
    
    # ─── Test-Cases synchronisieren ─────────────────────────
    if "test_cases" in body:
        # Alle alten Test-Cases löschen
        for tc in task.test_cases:
            session.delete(tc)
        
        # Neue Test-Cases erstellen
        for tc_data in body["test_cases"]:
            if tc_data.get("name") or tc_data.get("code"):
                tc = TestCase(
                    task_id=task_id,
                    name=tc_data.get("name", ""),
                    code=tc_data.get("code", ""),
                    expected_output=tc_data.get("expected_output", ""),
                    visibility=tc_data.get("visibility", "public"),
                    input_data=tc_data.get("input_data"),
                )
                session.add(tc)
    
    task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.commit()
    session.refresh(task)
    
    return {"message": "Aufgabe aktualisiert.", "task": {"id": task.id, "title": task.title}}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aufgabe löschen (nur Tutor/Admin des Kurses)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    # Prüfen, ob User den Kurs der Aufgabe bearbeiten darf
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung, diese Aufgabe zu löschen.")
    
    # Delete related test cases first
    for tc in task.test_cases:
        session.delete(tc)
    for sub in task.submissions:
        for fb in sub.feedback_list:
            session.delete(fb)
        session.delete(sub)
    
    session.delete(task)
    session.commit()
    return {"message": "Aufgabe '" + task.title + "' gelöscht."}


# ═══════════════════════════════════════════════════════════════════
# TEST-CASES
# ═══════════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/tests")
async def add_test_case(
    task_id: int,
    data: TestCaseCreate,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Neuen Test-Case zu Aufgabe hinzufügen."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    _check_course_role(user, task.course_id, session)
    
    tc = TestCase(
        task_id=task_id,
        name=data.name,
        code=data.code,
        expected_output=data.expected_output,
        visibility=data.visibility,
        input_data=data.input_data,
    )
    session.add(tc)
    session.commit()
    session.refresh(tc)
    return {"message": "Test-Case hinzugefügt.", "test_case": {"id": tc.id, "name": tc.name}}


# ═══════════════════════════════════════════════════════════════════
# LLM-ASSISTED CREATION
# ═══════════════════════════════════════════════════════════════════

@router.post("/courses/{course_id}/tasks/ai-suggest")
async def ai_suggest_task(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """
    LLM generiert Aufgabenvorschlag.
    
    Request:
        {
            "topic": "Rekursion",
            "difficulty": "mittel",
            "task_type": "code",
            "context": "Studenten kennen bereits Listen und Funktionen",
        }
    """
    body = await request.json()
    
    result = await llm_service.suggest_task(
        topic=body.get("topic", ""),
        difficulty=body.get("difficulty", "mittel"),
        task_type=body.get("task_type", "text"),
        title=body.get("title", ""),
        context=body.get("context", ""),
    )
    
    if not result.get("success"):
        raise HTTPException(500, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")
    
    return {
        "suggestion": result.get("data", {}),
        "latency_ms": result.get("latency_ms", 0),
    }


@router.post("/tasks/{task_id}/generate-solution")
async def generate_model_solution(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    LLM generiert Musterlösung für eine Aufgabe.
    
    task_id=0 bedeutet: neue Aufgabe (noch kein Task in DB).
    
    Request:
        {
            "description": "...",
            "task_type": "text",
            "max_points": 10,
            "code_template": "...",
        }
    """
    body = await request.json()

    # Zugriff prüfen
    if task_id > 0:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Aufgabe nicht gefunden.")
        course_id = task.course_id
    else:
        # Neue Aufgabe: course_id aus body
        course_id = body.get("course_id")
        if not course_id:
            raise HTTPException(400, "course_id required for new tasks.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung.")
    
    result = await llm_service.generate_model_solution(
        description=body.get("description", ""),
        task_type=body.get("task_type", "text"),
        max_points=body.get("max_points", 10),
        code_template=body.get("code_template", ""),
        title=body.get("title", ""),
    )
    
    if not result.get("success"):
        raise HTTPException(500, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")
    
    data = result.get("data", {})
    solution_text = data.get("model_solution", "") if isinstance(data, dict) else str(data)
    
    return {
        "solution": solution_text,
        "latency_ms": result.get("latency_ms", 0),
    }


# ═══════════════════════════════════════════════════════════════════
# EINSendungen & FEEDBACK
# ═══════════════════════════════════════════════════════════════════

@router.get("/tasks/{task_id}/submissions")
async def list_submissions(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Alle Einreichungen einer Aufgabe auflisten (Tutor-View)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    _check_course_role(user, task.course_id, session)
    
    submissions = session.exec(
        select(Submission).where(Submission.task_id == task_id)
    ).all()
    
    return [
        {
            "id": s.id,
            "student_id": s.student_id,
            "student_name": s.student.name if s.student else "unknown",
            "attempt_number": s.attempt_number,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "status": s.status.value,
            "feedback_count": len(s.feedback_list),
        }
        for s in submissions
    ]


@router.get("/tasks/{task_id}/submissions/{submission_id}")
async def get_submission(
    task_id: int,
    submission_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelne Einreichung mit Feedback laden."""
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(404, "Einreichung nicht gefunden.")
    
    task = session.get(Task, task_id)
    if not task or task.id != submission.task_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    _check_course_role(user, task.course_id, session)
    
    return {
        "id": submission.id,
        "student_id": submission.student_id,
        "student_name": submission.student.name if submission.student else "unknown",
        "solution": submission.solution,
        "code_solution": submission.code_solution,
        "attempt_number": submission.attempt_number,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "status": submission.status.value,
        "feedback": [
            {
                "id": f.id,
                "source": f.source.value,
                "points_earned": f.points_earned,
                "comment": f.comment,
                "giver": f.giver.name if f.giver else "LLM",
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in submission.feedback_list
        ],
    }


@router.post("/tasks/{task_id}/submissions/{submission_id}/feedback")
async def override_feedback(
    task_id: int,
    submission_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Tutor überschreibt/korrigiert LLM-Feedback.
    
    Request:
        {
            "points_earned": 8.5,
            "comment": "Gute Lösung, aber...",
        }
    """
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(404, "Einreichung nicht gefunden.")
    
    task = session.get(Task, task_id)
    if not task or task.id != submission.task_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    _check_course_role(user, task.course_id, session)
    
    body = await request.json()
    
    feedback = Feedback(
        submission_id=submission_id,
        source=FeedbackSource.HUMAN,
        giver_id=user.id,
        points_earned=float(body.get("points_earned", 0)),
        comment=body.get("comment", ""),
    )
    
    submission.status = SubmissionStatus.OVERRIDDEN
    session.add(feedback)
    session.add(submission)
    session.commit()
    session.refresh(feedback)
    
    return {
        "message": "Feedback überschrieben.",
        "feedback": {
            "id": feedback.id,
            "source": feedback.source.value,
            "points_earned": feedback.points_earned,
            "comment": feedback.comment,
        },
    }


@router.get("/courses/{course_id}/tasks/{task_id}/students/{student_id}/submissions")
async def get_student_submissions(
    course_id: int,
    task_id: int,
    student_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Lädt alle Einreichungen eines Students für eine Aufgabe — für die Tutor-Bewertungsseite.
    Enthält Aufgabenstellung, Lösungen, und alle Feedback-Einträge (LLM + Human).
    """
    # Berechtigungscheck
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Nur für Tutor/Admin.")

    task = session.get(Task, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    student = session.get(User, student_id)
    if not student:
        raise HTTPException(404, "Student nicht gefunden.")

    # Student muss Kursmitglied sein
    student_membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == student_id)
        .where(UserCourse.course_id == course_id)
    ).first()
    if not student_membership:
        raise HTTPException(403, "Student ist kein Mitglied dieses Kurses.")

    # Alle Submissions des Students für diese Aufgabe
    submissions = session.exec(
        select(Submission)
        .where(Submission.task_id == task_id)
        .where(Submission.student_id == student_id)
        .order_by(Submission.submitted_at.asc())  # type: ignore[attr-defined]
    ).all()

    # Test-Case-Infos (für Code-Aufgaben)
    test_cases = session.exec(
        select(TestCase).where(TestCase.task_id == task_id)
    ).all()

    return {
        "task": {
            "id": task.id,
            "title": task.title,
            "task_type": task.task_type.value,
            "description": task.description,
            "model_solution": task.model_solution,
            "max_points": task.max_points,
            "code_template": task.code_template,
            "test_count": len(test_cases),
        },
        "student": {
            "id": student.id,
            "username": student.username,
            "name": student.name,
        },
        "course_id": course_id,
        "submissions": [
            {
                "id": s.id,
                "solution": s.solution,
                "code_solution": s.code_solution,
                "attempt_number": s.attempt_number,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
                "status": s.status.value,
                "feedback": [
                    {
                        "id": f.id,
                        "source": f.source.value,
                        "points_earned": f.points_earned,
                        "comment": f.comment,
                        "giver": f.giver.name if f.giver else "LLM",
                        "giver_id": f.giver_id,
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    }
                    for f in s.feedback_list
                ],
            }
            for s in submissions
        ],
    }


# ═══════════════════════════════════════════════════════════════════
# ÜBERSICHTSTABELLE + EXCEL-EXPORT
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/overview")
async def get_overview(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
    filter_text: Optional[str] = None,
    type_filter: Optional[str] = None,
):
    """
    Übersichtstabelle: Students × Tasks mit Punkten.
    
    Optionaler Filter nach Blatt / Aufgabentyp (im Titel).
    """
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")
    
    # Alle Students des Kurses
    members = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    
    students = [session.get(User, m.user_id) for m in members]
    students = [s for s in students if s]  # Filter deleted users
    
    # Alle Aufgaben des Kurses
    tasks = list(session.exec(
        select(Task).where(Task.course_id == course_id)
    ).all())
    
    # Filter nach Aufgabentyp anwenden
    if type_filter:
        tasks = [t for t in tasks if t.task_type.value == type_filter]
    
    # Filter nach Text (Titel) anwenden
    if filter_text:
        filter_lower = filter_text.lower()
        tasks = [t for t in tasks if filter_lower in t.title.lower()]
    
    # Punkte-Matrix berechnen: {student_id: {task_id: points}}
    # Menschliche Bewertung hat Vorrang vor LLM-Bewertung
    scores = {}
    has_override = {}  # {student_id: {task_id: bool}}
    for student in students:
        scores[student.id] = {}
        has_override[student.id] = {}
        for task in tasks:
            statement = (
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == student.id)
            )
            submissions = session.exec(statement).all()
            
            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            
            for sub in submissions:
                for fb in sub.feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)
            
            final_points = human_points if override_exists else llm_points
            scores[student.id][task.id] = final_points
            has_override[student.id][task.id] = override_exists
    
    return {
        "course": {"id": course.id, "name": course.name},
        "students": [
            {
                "id": s.id,
                "username": s.username,
                "name": s.name,
                "total_points": sum(scores.get(s.id, {}).values()),
            }
            for s in students
        ],
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type.value,
                "max_points": t.max_points,
            }
            for t in tasks
        ],
        "scores": scores,
        "has_override": has_override,
    }


@router.post("/courses/{course_id}/export-excel")
async def export_excel(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
    filter_text: Optional[str] = None,
):
    """Excel-Export der Übersichtstabelle."""
    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")
    
    # Same data as overview
    members = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    students = [session.get(User, m.user_id) for m in members]
    students = [s for s in students if s]
    
    tasks = list(session.exec(
        select(Task).where(Task.course_id == course_id)
    ).all())
    
    if filter_text:
        filter_lower = filter_text.lower()
        tasks = [t for t in tasks if filter_lower in t.title.lower()]
    
    scores = {}
    for student in students:
        scores[student.id] = {}
        for task in tasks:
            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            submissions = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == student.id)
            ).all()
            for sub in submissions:
                for fb in sub.feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)
            scores[student.id][task.id] = human_points if override_exists else llm_points
    
    # Generate Excel
    excel_bytes = export_service.generate_overview_bytes(
        course_name=course.name,
        students=students,
        tasks=tasks,
        scores=scores,
        session=session,
        filter_text=filter_text,
    )
    
    filename = f"tutor_punktestand_{course.name.replace(' ', '_')}.xlsx"
    
    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )