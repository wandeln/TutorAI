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
    HintExchange,
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

    max_points = body.get("max_points", 10)
    try:
        max_points = int(max_points)
    except (TypeError, ValueError):
        raise HTTPException(400, "Max. Punkte muss eine Zahl sein.")
    if max_points < 0:
        raise HTTPException(400, "Max. Punkte muss mindestens 0 sein.")
    
    task = Task(
        course_id=course_id,
        created_by=user.id,  # type: ignore[arg-type]
        title=body.get("title", ""),
        task_type=TaskType(body.get("task_type", "text")),
        description=body.get("description", ""),
        model_solution=model_solution,
        max_points=max_points,
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
    
    if "max_points" in body:
        mp = body["max_points"]
        try:
            mp = int(mp)
        except (TypeError, ValueError):
            raise HTTPException(400, "Max. Punkte muss eine Zahl sein.")
        if mp < 0:
            raise HTTPException(400, "Max. Punkte muss mindestens 0 sein.")
        task.max_points = mp
    if "max_attempts" in body:
        task.max_attempts = None if body["max_attempts"] in (None, "") else int(body["max_attempts"])
    if "deadline" in body:
        task.deadline = None if body["deadline"] in (None, "") else body["deadline"]
    if "code_template" in body:
        val = body["code_template"]
        task.code_template = None if val in (None, "") else val
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


@router.post("/tasks/{task_id}/duplicate")
async def duplicate_task(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aufgabe duplizieren (nur Tutor/Admin des Kurses).

    Kopiert alle Daten der Aufgabe (ohne Einreichungen, Feedback und Hints)
    und fügt die Kopie direkt hinter dem Original ein.
    """
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
        raise HTTPException(403, "Keine Berechtigung, diese Aufgabe zu duplizieren.")

    body = await request.json()
    new_title = (body.get("title") or "").strip()
    if not new_title:
        raise HTTPException(400, "Titel darf nicht leer sein.")

    # Kopie direkt hinter dem Original: alle Aufgaben danach um eins nach hinten schieben
    for other in session.exec(
        select(Task)
        .where(Task.course_id == task.course_id)
        .where(Task.display_order > task.display_order)
    ).all():
        other.display_order += 1
        session.add(other)

    new_task = Task(
        course_id=task.course_id,
        created_by=user.id,  # type: ignore[arg-type]
        title=new_title,
        task_type=task.task_type,
        description=task.description,
        model_solution=task.model_solution,
        max_points=task.max_points,
        max_attempts=task.max_attempts,
        deadline=task.deadline,
        code_template=task.code_template,
        test_code=task.test_code,
        is_visible=task.is_visible,
        hints_enabled=task.hints_enabled,
        display_order=task.display_order + 1,
    )
    session.add(new_task)
    session.commit()
    session.refresh(new_task)

    return {
        "message": f"Aufgabe '{new_task.title}' dupliziert.",
        "task": {
            "id": new_task.id,
            "title": new_task.title,
            "task_type": new_task.task_type.value,
            "display_order": new_task.display_order,
        },
    }


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


@router.post("/tasks/{task_id}/reset-own-submissions")
async def reset_own_submissions(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Loescht alle eigenen Einreichungen fuer eine Aufgabe (nur PROF/TUTOR).

    Ermoeoglicht Tutoren/PROFs, ihre eigenen Tests zurueckzusetzen, um
    die Aufgabe ausfuehrlicher testen zu koennen (Student-View).
    """
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()
    if not membership or membership.role_in_course not in (CourseRole.PROF, CourseRole.TUTOR):
        raise HTTPException(403, "Keine Berechtigung.")

    # Finde alle eigenen Einreichungen (inkl. Feedback)
    own_subs = session.exec(
        select(Submission)
        .where(Submission.task_id == task_id)
        .where(Submission.student_id == user.id)
    ).all()

    count = 0
    for sub in own_subs:
        for fb in sub.feedback_list:
            session.delete(fb)
        session.delete(sub)
        count += 1

    session.commit()
    return {"message": f"{count} Einreichung(en) zurueckgesetzt."}


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
    has_submitted = {}  # { student_id: { task_id: bool } }

    for student in students:
        scores[student.id] = {}
        has_override[student.id] = {}
        has_submitted[student.id] = {}
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
                has_submitted[student.id][task.id] = True
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
        "has_submitted": has_submitted,
        "max_total": max_total,
    }


# ═══════════════════════════════════════════════════════════════════
# LLM-ASSISTED CREATION
# ═══════════════════════════════════════════════════════════════════

@router.post("/courses/{course_id}/tasks/ai-generate")
async def ai_generate_task(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """
    LLM generiert/ändert die angeforderten Felder einer Aufgabe (einheitlich).

    Request:
        {
            "task_type": "code",
            "topic": "Rekursion",
            "difficulty": "mittel",
            "max_points": 10,
            "title": "...",
            "description": "...",
            "model_solution": "...",
            "code_template": "...",
            "generate_fields": {
                "title": true,
                "description": true,
                "solution": true,
                "template": true,   // nur Code-Aufgaben
                "tests": true        // nur Code-Aufgaben
            }
        }
    """
    body = await request.json()

    task_type = body.get("task_type", "text")
    gen = body.get("generate_fields", {}) or {}

    gen_title = bool(gen.get("title"))
    gen_description = bool(gen.get("description"))
    gen_solution = bool(gen.get("solution"))
    # Template/Tests gelten nur für Code-Aufgaben
    gen_template = bool(gen.get("template")) and task_type == "code"
    gen_tests = bool(gen.get("tests")) and task_type == "code"

    if not (gen_title or gen_description or gen_solution or gen_template or gen_tests):
        raise HTTPException(400, "Keine Felder angefordert.")

    current_title = (body.get("title") or "").strip()
    current_description = (body.get("description") or "").strip()
    current_solution = (body.get("model_solution") or "").strip()
    current_template = (body.get("code_template") or "").strip()

    # Schritt 1: Titel/Aufgabenstellung/Musterlösung — nur aktiv angeforderte Felder.
    # Nicht angeforderte Felder werden weder im Prompt verlangt noch übernommen.
    step1_fields: list[str] = []
    if gen_title:
        step1_fields.append("title")
    if gen_description:
        step1_fields.append("description")
    if gen_solution:
        step1_fields.append("model_solution")

    # Code-Aufgabe: Vorlage/Tests brauchen eine Musterlösung als Basis —
    # falls die Lösung nicht aktiv angefordert und noch leer, intern ergänzen
    # (wird aber NICHT in die Response übernommen).
    if (gen_template or gen_tests) and not gen_solution and not current_solution:
        step1_fields.append("model_solution")

    llm_cfg = get_effective_llm_config(session, user_and_course[1])

    response = {
        "title": "",
        "description": "",
        "model_solution": "",
        "code_template": "",
        "public_tests": "",
        "private_tests": "",
    }
    latency_ms = 0
    internal_solution_text = ""

    if step1_fields:
        result = await llm_service.generate_task_fields(
            topic=body.get("topic", ""),
            difficulty=body.get("difficulty", "mittel"),
            task_type=task_type,
            max_points=body.get("max_points", 10),
            generate_fields=step1_fields,
            current_title=current_title,
            current_description=current_description,
            current_model_solution=current_solution,
            code_template=current_template,
            config=llm_cfg,
        )

        if not result.get("success"):
            raise HTTPException(500, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

        data = result.get("data") or {}
        latency_ms = result.get("latency_ms", 0)

        # NUR angeforderte Felder aus der LLM-Antwort übernehmen.
        if "title" in step1_fields:
            response["title"] = (data.get("title") or "").strip()
        if "description" in step1_fields:
            response["description"] = (data.get("description") or "").strip()
        if "model_solution" in step1_fields:
            sol = (data.get("model_solution") or "").strip()
            if gen_solution:
                response["model_solution"] = sol
            else:
                internal_solution_text = sol

    # Basis für Schritt 2 (Code): neue Lösung, sonst intern generierte, sonst vorhandene
    solution_for_step2 = response["model_solution"] or internal_solution_text or current_solution

    # Schritt 2: Code-Vorlage + Tests (nur Code-Aufgaben, nur angefordert)
    if task_type == "code" and (gen_template or gen_tests):
        template_result = await llm_service.generate_code_template_and_tests(
            description=response["description"] or current_description,
            model_solution=solution_for_step2,
            config=llm_cfg,
        )

        if not template_result.get("success"):
            raise HTTPException(500, f"LLM-Fehler (Code/Tests): {template_result.get('error', 'Unbekannter Fehler')}")

        template_data = template_result.get("data") or {}
        latency_ms = latency_ms + template_result.get("latency_ms", 0)

        if gen_template:
            response["code_template"] = template_data.get("code_template", "")
        if gen_tests:
            response["public_tests"] = template_data.get("public_tests", "")
            response["private_tests"] = template_data.get("private_tests", "")

    response["latency_ms"] = latency_ms
    return response


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
            "solve_time_seconds": sub.solve_time_seconds,
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
    type_filter = request.query_params.get("type_filter", "").strip()

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
    if type_filter:
        tasks = [t for t in tasks if t.task_type.value == type_filter]

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


# ═══════════════════════════════════════════════════════════════════
# KURS-PERFORMANCE-REPORT (LLM)
# ═══════════════════════════════════════════════════════════════════

@router.post("/courses/{course_id}/generate-report")
async def generate_course_report(
    course_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user_and_course: tuple[User, int] = Depends(require_course_access(CourseRole.PROF, CourseRole.TUTOR)),
):
    """Generiert einen detaillierten Performance-Report für den Kurs via LLM.

    Sammelt alle Daten der gefilterten Aufgaben (Aufgaben, Submissions, Feedback,
    Hinweise) und lässt das LLM einen strukturierten Markdown-Report erstellen.
    """
    user, _ = user_and_course

    course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Kurs nicht gefunden.")

    # Filter aus Query-Parametern (identisch zu get_course_overview)
    filter_text = request.query_params.get("filter_text", "").strip()
    type_filter = request.query_params.get("type_filter", "").strip()

    # Alle sichtbaren Aufgaben des Kurses
    tasks = list(session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .where(Task.is_visible == True)
        .order_by(Task.display_order.asc())  # type: ignore[attr-defined]
    ).all())

    if filter_text:
        tasks = [t for t in tasks if filter_text.lower() in t.title.lower()]
    if type_filter:
        tasks = [t for t in tasks if t.task_type.value == type_filter]

    if not tasks:
        raise HTTPException(400, "Keine Aufgaben gefunden. Passe den Filter an.")

    # Alle Studenten des Kurses
    memberships = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    students = [m.user for m in memberships]

    # ── Daten für das LLM sammeln ──────────────────────────────

    # Aufgaben-Informationen formatieren
    tasks_lines = []
    for t in tasks:
        tasks_lines.append(f"- **{t.title}** ({t.task_type.value}) — {t.max_points} Punkte, Max. Versuche: {t.max_attempts or 'unlimitiert'}")
        tasks_lines.append(f"  Aufgabenstellung: {t.description[:500]}")
        if t.model_solution:
            tasks_lines.append(f"  Musterlösung: {t.model_solution[:500]}")
        tasks_lines.append("")
    tasks_data = "\n".join(tasks_lines)

    # Studentendaten formatieren (Submissions + Feedback + Hinweise)
    students_lines = []
    for student in students:
        students_lines.append(f"### {student.name} ({student.username})")

        has_submissions = False
        for task in tasks:
            # Alle Submissions für diesen Student bei dieser Aufgabe
            subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == student.id)
                .order_by(Submission.submitted_at.asc())  # type: ignore[attr-defined]
            ).all()

            if not subs:
                continue

            has_submissions = True
            best_points = 0.0
            total_attempts = len(subs)
            latest_sub = subs[-1]

            # Punkte aus Feedback sammeln
            for fb in latest_sub.feedback_list:
                if fb.source == FeedbackSource.HUMAN:
                    best_points = max(best_points, fb.points_earned)
                else:
                    best_points = max(best_points, fb.points_earned)

            # Solve-Zeiten
            solve_times = [s.solve_time_seconds for s in subs if s.solve_time_seconds > 0]
            avg_time = sum(solve_times) / len(solve_times) if solve_times else 0
            max_time = max(solve_times) if solve_times else 0

            students_lines.append("")
            students_lines.append(f"- **{task.title}**:")
            students_lines.append(f"  Punkte: {best_points}/{task.max_points}, Versuche: {total_attempts}")
            if solve_times:
                students_lines.append(f"  Bearbeitungsdauer: Ø {avg_time/60:.1f}min, Max {max_time/60:.1f}min")

            # Feedback-Kommentare der letzten Submission
            for fb in latest_sub.feedback_list:
                source_label = "LLM" if fb.source == FeedbackSource.LLM else "Tutor"
                fb_comment = fb.comment[:300] if fb.comment else "(keinen Kommentar)"
                students_lines.append(f"  [{source_label}]: {fb_comment}")

            # Hinweis-Anfragen für diese Aufgabe
            hints = session.exec(
                select(HintExchange)
                .where(HintExchange.task_id == task.id)
                .where(HintExchange.student_id == student.id)
            ).all()
            if hints:
                students_lines.append(f"  Hinweise angefragt: {len(hints)} mal")
                for h in hints[:3]:
                    students_lines.append(f"    Frage: {h.question[:200]}")
                    students_lines.append(f"    Antwort: {h.llm_response[:200]}")

        if not has_submissions:
            students_lines.append("  **Keine Einreichungen**")

        students_lines.append("")

    students_data = "\n".join(students_lines)

    # LLM-Config ermitteln
    llm_config = get_effective_llm_config(session, course_id)

    # Report generieren
    logger.info(f"Generate course report for {course.name}: {len(tasks)} tasks, {len(students)} students")
    result = await llm_service.generate_course_report(
        course_name=course.name,
        tasks_data=tasks_data,
        students_data=students_data,
        config=llm_config,
    )

    if not result["success"]:
        raise HTTPException(
            500,
            f"Report-Generierung fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}",
        )

    return {
        "success": True,
        "report": result["data"]["report"],
        "latency_ms": result["latency_ms"],
    }