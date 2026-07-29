"""
Student-Endpoints: Aufgaben ansehen, loesen, Feedback einsehen.

Rolle: Student (im Kurs)
"""

import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    User, Task, TestCase, Submission, Feedback,
    TaskType, SubmissionStatus, FeedbackSource, TestVisibility,
    Course, UserCourse, UserRole,
)
from services.auth_service import get_current_user
from services.grading_service import GradingService
from services.llm_service import LLMService
from services.sandbox_runner import SandboxedRunner

router = APIRouter(prefix="/api/student", tags=["Student"])
grading_service = GradingService()
sandbox_runner = SandboxedRunner()
llm = LLMService()


# =================================================================
# KURS-AUFGABEN
# =================================================================

@router.get("/courses/{course_id}/tasks")
async def list_course_tasks(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Aktuelle Aufgaben eines Kurses (Student-View)."""
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership or membership.role_in_course != UserRole.STUDENT:
        raise HTTPException(403, "Kein Zugriff auf diesen Kurs.")

    tasks = session.exec(
        select(Task)
        .where(Task.course_id == course_id)
        .order_by(Task.created_at.desc())
    ).all()

    result = []
    for task in tasks:
        my_submissions = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
        ).all()

        best_points = 0
        best_feedback = None
        for sub in my_submissions:
            for fb in sub.feedback_list:
                if fb.points_earned > best_points:
                    best_points = fb.points_earned
                    best_feedback = fb

        deadline_passed = False
        if task.deadline:
            deadline_dt = datetime.fromisoformat(task.deadline)
            deadline_passed = datetime.now() > deadline_dt

        attempts_used = len(my_submissions)
        max_reached = task.max_attempts and attempts_used >= task.max_attempts

        can_submit = not deadline_passed and not max_reached

        result.append({
            "id": task.id,
            "title": task.title,
            "task_type": task.task_type.value,
            "max_points": task.max_points,
            "my_points": best_points,
            "attempts_used": attempts_used,
            "max_attempts": task.max_attempts,
            "deadline": task.deadline,
            "deadline_passed": deadline_passed,
            "can_submit": can_submit,
            "has_feedback": best_feedback is not None,
        })

    return result


@router.get("/tasks/{task_id}")
async def get_task_detail(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Einzelne Aufgabe laden (Student-View: keine Musterloesung!)."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff auf diese Aufgabe.")

    public_tests = [
        tc for tc in task.test_cases
        if tc.visibility == TestVisibility.PUBLIC
    ]

    return {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type.value,
        "description": task.description,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "deadline": task.deadline,
        "code_template": task.code_template if task.task_type.value == "code" else None,
        "public_tests": [
            {
                "id": tc.id,
                "name": tc.name,
                "code": tc.code,
            }
            for tc in public_tests
        ],
    }


# =================================================================
# EINSendungen
# =================================================================

@router.post("/tasks/{task_id}/submit")
async def submit_solution(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Loesung einreichen -> LLM korrigiert + Feedback.

    Request (je nach Typ):
        Text: { "solution": "..." }
        Code: { "code_solution": "..." }
    """
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff.")

    deadline_passed = False
    if task.deadline:
        deadline_dt = datetime.fromisoformat(task.deadline)
        deadline_passed = datetime.now() > deadline_dt

    if deadline_passed:
        raise HTTPException(400, f"Deadline ({task.deadline}) ueberschritten.")

    existing = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == user.id)
    ).all()

    attempts_used = len(existing)
    if task.max_attempts and attempts_used >= task.max_attempts:
        raise HTTPException(
            400,
            f"Maximale Anzahl Versuche ({task.max_attempts}) erreicht.",
        )

    body = await request.json()
    solution = body.get("solution", "")
    code_solution = body.get("code_solution", "")

    submission = Submission(
        task_id=task.id,
        student_id=user.id,
        solution=solution,
        code_solution=code_solution,
        attempt_number=attempts_used + 1,
    )
    session.add(submission)
    session.commit()
    session.refresh(submission)

    custom_prompt = None

    grading_result = await grading_service.grade_submission(
        task=task,
        submission=submission,
        session=session,
        custom_prompt=custom_prompt,
    )

    # Bisher beste Punktzahl (inkl. dieser Submission)
    best_points = 0
    for sub in existing:
        for fb in sub.feedback_list:
            if fb.points_earned > best_points:
                best_points = fb.points_earned
    for fb in submission.feedback_list:
        if fb.points_earned > best_points:
            best_points = fb.points_earned

    total_attempts = attempts_used + 1

    # Feedback-Text als String (nicht das Feedback-Objekt!)
    feedback_text = grading_result.get("comment", "")

    response = {
        "message": "Loesung eingereicht und korrigiert.",
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "points": grading_result.get("points", 0),
        "max_points": task.max_points,
        "feedback": feedback_text,
        "best_points": best_points,
        "total_attempts": total_attempts,
        "max_attempts": task.max_attempts,
        "remaining_attempts": max(0, (task.max_attempts or 999) - total_attempts),
    }

    if task.task_type.value == "code" and "test_results" in grading_result:
        response["tests_passed"] = grading_result.get("tests_passed", 0)
        response["tests_total"] = grading_result.get("tests_total", 0)
        response["test_results"] = grading_result.get("test_results", [])

    return response


@router.post("/tasks/{task_id}/run-tests")
async def run_public_tests(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Public Tests ausfuehren (server-seitig, sandbox).

    Kein LLM-Grading — nur Test-Ergebnisse fuer schnelles Feedback.
    """
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    if task.task_type.value != "code":
        raise HTTPException(400, "Nur bei Code-Aufgaben verfuegbar.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff.")

    public_tests = session.exec(
        select(TestCase)
        .where(TestCase.task_id == task.id)
        .where(TestCase.visibility == TestVisibility.PUBLIC)
    ).all()

    if not public_tests:
        return {"message": "Keine Public Tests fuer diese Aufgabe.", "test_results": []}

    body = await request.json()
    code = body.get("code_solution", "")

    if not code.strip():
        raise HTTPException(400, "Kein Code eingereicht.")

    tests_code = "\n\n".join(tc.code for tc in public_tests)

    result = await sandbox_runner.run(code=code, tests_code=tests_code)

    return {
        "passed": result.get("passed", False),
        "test_results": result.get("test_results", []),
        "tests_passed": sum(1 for t in result.get("test_results", []) if t.get("passed")),
        "tests_total": len(public_tests),
        "stderr": result.get("stderr", ""),
        "timeout": result.get("timeout", False),
    }


# =================================================================
# FEEDBACK & PUNKTE
# =================================================================

@router.get("/courses/{course_id}/my-submissions")
async def get_my_submissions(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Alle eigenen Einreichungen eines Kurses mit Feedback.
    """
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff auf diesen Kurs.")

    tasks = session.exec(
        select(Task).where(Task.course_id == course_id)
    ).all()

    submissions = []
    for task in tasks:
        my_subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
        ).all()

        for sub in my_subs:
            submissions.append({
                "task_id": task.id,
                "task_title": task.title,
                "submission_id": sub.id,
                "attempt_number": sub.attempt_number,
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                "status": sub.status.value,
                "feedback": [
                    {
                        "id": f.id,
                        "source": f.source.value,
                        "points_earned": f.points_earned,
                        "comment": f.comment,
                        "giver": f.giver.name if f.giver else "LLM",
                        "created_at": f.created_at.isoformat() if f.created_at else None,
                    }
                    for f in sub.feedback_list
                ],
            })

    return submissions


@router.get("/courses/{course_id}/my-points")
async def get_my_points(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Gesamt-Punktzahl des Students im Kurs.
    """
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff.")

    tasks = session.exec(
        select(Task).where(Task.course_id == course_id)
    ).all()

    total_points = 0
    total_possible = 0
    task_points = []

    for task in tasks:
        my_subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
        ).all()

        best_points = 0
        for sub in my_subs:
            for fb in sub.feedback_list:
                if fb.points_earned > best_points:
                    best_points = fb.points_earned

        total_points += best_points
        total_possible += task.max_points

        task_points.append({
            "task_title": task.title,
            "earned": best_points,
            "max": task.max_points,
        })

    percentage = (total_points / total_possible * 100) if total_possible > 0 else 0

    return {
        "total_points": total_points,
        "total_possible": total_possible,
        "percentage": round(percentage, 1),
        "task_points": task_points,
    }


# =================================================================
# BILD -> LATEX KONVERTIERUNG
# =================================================================

@router.post("/latex-from-image")
async def latex_from_image(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Konvertiert ein hochgeladenes Foto einer handgeschriebenen Formel
    in LaTeX-Code via multimodalem LLM.

    Request: { "image_base64": "...", "mime_type": "image/png" }
    """
    body = await request.json()
    image_base64 = body.get("image_base64", "")
    mime_type = body.get("mime_type", "image/png")

    if not image_base64:
        raise HTTPException(400, "Kein Bild uebergeben.")

    # Validate mime_type
    allowed_mimes = ["image/png", "image/jpeg", "image/webp", "image/gif"]
    if mime_type not in allowed_mimes:
        raise HTTPException(400, f"Nicht unterstuetztes Bildformat. Erlaubt: {', '.join(allowed_mimes)}")

    # Basic size check (base64 of 10MB image ~ 13MB text)
    max_base64_length = 13_312_000
    if len(image_base64) > max_base64_length:
        raise HTTPException(400, "Bild zu gross. Maximum sind ca. 10 MB.")

    result = await llm.convert_image_to_latex(
        image_base64=image_base64,
        mime_type=mime_type,
    )

    if not result["success"]:
        raise HTTPException(
            503,
            f"LLM-Konvertierung fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}",
        )

    return {
        "latex": result["data"]["latex"],
        "latency_ms": result["latency_ms"],
    }