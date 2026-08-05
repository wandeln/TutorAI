"""
Tutor-Endpoints: Aufgaben-Management + Korrektur + Übersicht.

Rollen: Tutor und PROF (im Kurs), Admin (global)
"""

import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, SQLModel, select

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
    User,
    UserCourse,
)
from services.auth_service import get_current_user, require_course_access
from services.export_service import ExportService
from services.grading_service import GradingService
from services.llm_service import LLMService
from services.settings_resolver import get_effective_llm_config

router = APIRouter(prefix="/api", tags=["Tutor"])
grading_service = GradingService()
llm_service = LLMService()
export_service = ExportService()
logger = logging.getLogger(__name__)


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
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).all())
    
    return [
        {
            "id": t.id,
            "title": t.title,
            "task_type": t.task_type.value,
            "max_points": t.max_points,
            "max_attempts": t.max_attempts,
            "deadline": t.deadline,
            "has_tests": bool(t.test_code),
            "submission_count": len(t.submissions),
            "is_visible": t.is_visible,
            "hints_enabled": t.hints_enabled,
            "display_order": t.display_order,
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

    # Bestimme naechsten display_order-Wert (am Ende der Liste)
    existing_tasks = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .order_by(Task.display_order.desc())  # type: ignore[attr-defined]
    ).all()
    next_order = existing_tasks[0].display_order + 1 if existing_tasks else 0

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
        test_code=body.get("test_code"),
        is_visible=body.get("is_visible", True),
        display_order=next_order,
    )
    
    session.add(task)
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
    """Einzelne Aufgabe laden."""
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
    
    result = {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type.value,
        "description": task.description,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "deadline": task.deadline,
        "code_template": task.code_template if task.task_type.value == "code" else None,
        "test_code": task.test_code if is_tutor else None,
        "hints_enabled": task.hints_enabled,
    }
    
    if is_tutor:
        result["model_solution"] = task.model_solution
    else:
        # Student: mask private tests (replace PrivateTest class body with *** )
        if task.test_code:
            import re
            result["test_code"] = re.sub(
                r'(class PrivateTest.*?)(class PublicTest|$)',
                r'\1***\2',
                task.test_code,
                flags=re.DOTALL
            )
    
    return result


@router.put("/tasks/{task_id}")
async def update_task(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aufgabe bearbeiten."""
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
    if "test_code" in body: task.test_code = body["test_code"]
    if "is_visible" in body: task.is_visible = body["is_visible"]
    if "hints_enabled" in body: task.hints_enabled = body["hints_enabled"]
    
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
    
    for sub in task.submissions:
        for fb in sub.feedback_list:
            session.delete(fb)
        session.delete(sub)
    
    session.delete(task)
    session.commit()
    return {"message": "Aufgabe '" + task.title + "' gelöscht."}


@router.patch("/tasks/{task_id}/visibility")
async def toggle_task_visibility(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Sichtbarkeit einer Aufgabe umschalten (nur PROF/TUTOR)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung, die Sichtbarkeit zu ändern.")
    
    body = await request.json()
    new_visibility = body.get("is_visible", not task.is_visible)
    task.is_visible = new_visibility
    
    session.add(task)
    session.commit()
    session.refresh(task)
    
    status_msg = "sichtbar" if task.is_visible else "versteckt"
    return {"message": f"Aufgabe ist jetzt {status_msg}.", "is_visible": task.is_visible}


class TaskReorderRequest(SQLModel):
    task_ids: list[int]


@router.patch("/courses/{course_id}/tasks/reorder")
async def reorder_tasks(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Reihenfolge der Aufgaben in einem Kurs aendern (nur PROF/TUTOR)."""
    _user, _cid = user_and_course

    body = await request.json()
    task_ids = body.get("task_ids", [])

    if not task_ids:
        return {"message": "Keine Aufgaben zum Verschieben angegeben."}

    # Bestaetige, dass alle Aufgaben zum Kurs gehoeren
    for tid in task_ids:
        task = session.get(Task, tid)
        if not task or task.course_id != course_id:
            raise HTTPException(400, f"Aufgabe {tid} gehoert nicht zu diesem Kurs.")

    # Reihenfolge aktualisieren
    for idx, tid in enumerate(task_ids):
        task = session.get(Task, tid)
        if task:
            task.display_order = idx
            session.add(task)

    session.commit()

    return {"message": "Aufgaben-Reihenfolge aktualisiert."}


@router.get("/courses/{course_id}/overview")
async def get_course_overview(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Übersichtstabelle: Alle Studenten x Aufgaben mit Punkten."""
    user, _ = user_and_course

    # Alle sichtbaren Aufgaben des Kurses
    tasks = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.is_visible == True)
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).all()

    # Alle Studenten des Kurses
    memberships = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    students = [m.user for m in memberships]

    # Filter aus Query-Parametern
    filter_text = request.query_params.get("filter_text", "").strip()
    type_filter = request.query_params.get("type_filter", "").strip()

    if filter_text:
        tasks = [t for t in tasks if filter_text.lower() in t.title.lower()]
    if type_filter:
        tasks = [t for t in tasks if t.task_type.value == type_filter]

    # Scores berechnen
    scores = {}  # { student_id: { task_id: points } }
    has_override = {}  # { student_id: { task_id: bool } }

    for student in students:
        scores[student.id] = {}
        has_override[student.id] = {}
        for task in tasks:
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == student.id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
            ).all()

            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            if subs:
                latest_sub = subs[0]  # newest
                for fb in latest_sub.feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)

            point_val = human_points if override_exists else llm_points
            scores[student.id][task.id] = point_val
            has_override[student.id][task.id] = override_exists

    # Gesamtprozent pro Student
    max_total = sum(t.max_points for t in tasks)
    student_list = []
    for student in students:
        total = sum(scores[student.id].get(t.id, 0) for t in tasks)
        student_list.append({
            "id": student.id,
            "username": student.username,
            "name": student.name,
            "total_points": total,
        })

    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type.value,
                "max_points": t.max_points,
            }
            for t in tasks
        ],
        "students": student_list,
        "scores": scores,
        "has_override": has_override,
        "max_total": max_total,
    }


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
    
    llm_cfg = get_effective_llm_config(session, user_and_course[1])
    result = await llm_service.suggest_task(
        topic=body.get("topic", ""),
        difficulty=body.get("difficulty", "mittel"),
        task_type=body.get("task_type", "text"),
        title=body.get("title", ""),
        context=body.get("context", ""),
        config=llm_cfg,
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
    
    Request:
        {
            "course_id": 1,
            "description": "...",
            "task_type": "code",
            "max_points": 10,
            "code_template": "...",
        }
    """
    # Body lesen (immer nötig, um die Felder zu erhalten)
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

    task_type = body.get("task_type", "text")
    generate_fields = body.get("generate_fields", {})

    llm_cfg = get_effective_llm_config(session, course_id)

    if task_type == "code":
        # Zwei-Schritt-Prozess für Code-Aufgaben:
        # 1. Zuerst Musterlösung generieren
        # 2. Dann Code-Vorlage + Tests basierend auf Musterlösung

        # Schritt 1: Musterlösung
        solution_result = await llm_service.generate_model_solution(
            description=body.get("description", ""),
            task_type="code",
            max_points=body.get("max_points", 10),
            code_template=body.get("code_template", ""),
            title=body.get("title", ""),
            config=llm_cfg,
        )

        if not solution_result.get("success"):
            raise HTTPException(500, f"LLM-Fehler (Musterlösung): {solution_result.get('error', 'Unbekannter Fehler')}")

        solution_data = solution_result.get("data", {})
        solution_text = solution_data.get("model_solution", "") if isinstance(solution_data, dict) else str(solution_data)
        total_latency = solution_result.get("latency_ms", 0)

        response = {
            "solution": solution_text,
            "code_template": "",
            "public_tests": "",
            "private_tests": "",
            "latency_ms": total_latency,
        }

        # Schritt 2: Code-Vorlage + Tests (nur wenn angefordert)
        if generate_fields.get("template") or generate_fields.get("publicTests") or generate_fields.get("privateTests"):
            template_result = await llm_service.generate_code_template_and_tests(
                description=body.get("description", ""),
                model_solution=solution_text,
                config=llm_cfg,
            )

            if template_result.get("success"):
                template_data = template_result.get("data", {})
                total_latency += template_result.get("latency_ms", 0)
                response["latency_ms"] = total_latency

                if generate_fields.get("template"):
                    response["code_template"] = template_data.get("code_template", "")
                if generate_fields.get("publicTests"):
                    response["public_tests"] = template_data.get("public_tests", "")
                if generate_fields.get("privateTests"):
                    response["private_tests"] = template_data.get("private_tests", "")

        return response

    else:
        # Text-Aufgaben: nur Musterlösung
        result = await llm_service.generate_model_solution(
            description=body.get("description", ""),
            task_type=task_type,
            max_points=body.get("max_points", 10),
            code_template=body.get("code_template", ""),
            title=body.get("title", ""),
            config=llm_cfg,
        )

        if not result.get("success"):
            raise HTTPException(500, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

        solution_text = result.get("data", {}).get("model_solution", "")
        return {
            "solution": solution_text,
            "latency_ms": result.get("latency_ms", 0),
        }


# ═══════════════════════════════════════════════════════════════════
# SUBMISSION REVIEW
# ═══════════════════════════════════════════════════════════════════

@router.get("/courses/{course_id}/tasks/{task_id}/students")
async def get_task_students(
    course_id: int,
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Gibt alle Studenten eines Kurses zurück, die für eine Aufgabe Einreichungen haben."""
    # Zugriff prüfen
    _check_course_role(user, course_id, session)

    task = session.get(Task, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    # Alle Studenten des Kurses
    memberships = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    students = [m.user for m in memberships]

    # Für jeden Student prüfen, ob Einreichungen existieren
    result = []
    for student in students:
        subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task_id)
            .where(Submission.student_id == student.id)
            .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
        ).all()

        has_submissions = len(subs) > 0
        has_override = False
        latest_points = 0.0

        if subs:
            latest_sub = subs[0]
            human_points = 0.0
            llm_points = 0.0
            for fb in latest_sub.feedback_list:
                if fb.source == FeedbackSource.HUMAN:
                    human_points = max(human_points, fb.points_earned)
                    has_override = True
                elif fb.source == FeedbackSource.LLM:
                    llm_points = max(llm_points, fb.points_earned)
            latest_points = human_points if has_override else llm_points

        result.append({
            "id": student.id,
            "name": student.name,
            "username": student.username,
            "has_submissions": has_submissions,
            "latest_points": latest_points,
            "has_override": has_override,
        })

    # Sort by name
    result.sort(key=lambda s: s["name"].lower())

    return {"students": result}


@router.get("/courses/{course_id}/tasks/{task_id}/students/{student_id}/submissions")
async def get_student_submissions(
    course_id: int,
    task_id: int,
    student_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Gibt alle Einreichungen eines Students für eine Aufgabe zurück (für Tutor-Bewertung)."""
    # Zugriff prüfen
    _check_course_role(user, course_id, session)

    task = session.get(Task, task_id)
    if not task or task.course_id != course_id:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    student = session.get(User, student_id)
    if not student:
        raise HTTPException(404, "Student nicht gefunden.")

    # Submissions laden
    subs = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == student.id)
        .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
    ).all()

    submissions = []
    for sub in subs:
        feedback = []
        for fb in sub.feedback_list:
            giver_name = None
            if fb.giver_id:
                giver = session.get(User, fb.giver_id)
                if giver:
                    giver_name = giver.name
            elif fb.source == FeedbackSource.LLM:
                giver_name = "LLM"

            feedback.append({
                "id": fb.id,
                "source": fb.source.value,
                "points_earned": fb.points_earned,
                "comment": fb.comment,
                "giver": giver_name,
                "created_at": fb.created_at.isoformat() if fb.created_at else "",
            })

        submissions.append({
            "id": sub.id,
            "solution": sub.solution,
            "code_solution": sub.code_solution,
            "attempt_number": sub.attempt_number,
            "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else "",
            "status": sub.status.value,
            "feedback": feedback,
        })

    return {
        "task": {
            "id": task.id,
            "title": task.title,
            "task_type": task.task_type.value,
            "description": task.description,
            "model_solution": task.model_solution,
            "max_points": task.max_points,
        },
        "student": {
            "id": student.id,
            "name": student.name,
            "username": student.username,
        },
        "submissions": submissions,
    }


@router.post("/tasks/{task_id}/submissions/{submission_id}/feedback")
async def add_feedback_to_submission(
    task_id: int,
    submission_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Tutor gibt Feedback fuer eine Einreichung.
    Wenn der Tutor bereits eine Bewertung fuer diese Einreichung hat, wird diese aktualisiert.
    Ansonsten wird eine neue angelegt.
    """
    submission = session.get(Submission, submission_id)
    if not submission or submission.id is None or submission.task_id != task_id:
        raise HTTPException(404, "Einreichung nicht gefunden.")

    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    # Zugriff prüfen
    _check_course_role(user, task.course_id, session)

    body = await request.json()
    points = float(body.get("points_earned", 0))
    comment = body.get("comment", "").strip()

    if not comment:
        raise HTTPException(400, "Feedback-Text darf nicht leer sein.")

    if points < 0 or points > task.max_points:
        raise HTTPException(400, f"Punkte muessen zwischen 0 und {task.max_points} liegen.")

    # Pro Einreichung maximal eine Tutor-Bewertung.
    # Wenn eine existiert: loeschen und neu erstellen (damit giver_id und created_at korrekt sind)
    existing_feedbacks = session.exec(
        select(Feedback)
        .where(Feedback.submission_id == submission.id)
        .where(Feedback.source == FeedbackSource.HUMAN)
    ).all()

    for existing in existing_feedbacks:
        session.delete(existing)

    # Neue Bewertung anlegen
    feedback = Feedback(
        submission_id=submission.id,
        source=FeedbackSource.HUMAN,
        points_earned=points,
        comment=comment,
        giver_id=user.id,
    )
    session.add(feedback)

    # Status auf OVERRIDDEN setzen
    submission.status = SubmissionStatus.OVERRIDDEN
    session.add(submission)

    session.commit()
    session.refresh(feedback)

    # Berechne latest_points des Students für diese Aufgabe (wie student.py)
    student_subs = session.exec(
        select(Submission)
        .where(Submission.task_id == task_id)
        .where(Submission.student_id == submission.student_id)
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

    return {
        "id": feedback.id,
        "source": feedback.source.value,
        "points_earned": feedback.points_earned,
        "comment": feedback.comment,
        "giver": user.name,
        "created_at": feedback.created_at.isoformat() if feedback.created_at else "",
        "latest_points": latest_points,
        "max_points": task.max_points,
    }


# ═══════════════════════════════════════════════════════════════════
# EXCEL-EXPORT
# ═══════════════════════════════════════════════════════════════════

@router.post("/courses/{course_id}/export-excel")
async def export_excel(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Excel-Export der Übersichtstabelle: Alle Studenten x Aufgaben mit Punkten."""
    _, _ = user_and_course

    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    # Query-Parameter
    filter_text = request.query_params.get("filter_text", "").strip()

    # Alle sichtbaren Aufgaben des Kurses
    tasks = list(session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.is_visible == True)
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).all())

    # Filter nach Suchbegriff
    if filter_text:
        tasks = [t for t in tasks if filter_text.lower() in t.title.lower()]

    # Alle Studenten des Kurses
    memberships = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    students = [m.user for m in memberships]

    # Scores berechnen
    scores = {}  # { student_id: { task_id: points } }
    for student in students:
        scores[student.id] = {}
        for task in tasks:
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == student.id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
            ).all()

            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            if subs:
                latest_sub = subs[0]
                for fb in latest_sub.feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        human_points = max(human_points, fb.points_earned)
                        override_exists = True
                    else:
                        llm_points = max(llm_points, fb.points_earned)

            point_val = human_points if override_exists else llm_points
            scores[student.id][task.id] = point_val

    # Excel-Datei generieren
    excel_bytes = export_service.generate_overview_bytes(
        course_name=course.name,
        students=students,
        tasks=tasks,
        scores=scores,
        session=session,
        filter_text=filter_text or None,
    )

    filename = f"punktestand_{course.name.replace(' ', '_')}.xlsx"
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )