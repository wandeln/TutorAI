"""
Student-Endpoints: Aufgaben ansehen, Lösungen einreichen, Tests ausführen.

Rolle: Student (im Kurs)
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from database.base import get_session
from database.models import (
    User, Task, Submission, Feedback,
    TaskType, SubmissionStatus, FeedbackSource,
    Course, UserCourse, CourseRole,
)
from services.auth_service import get_current_user
from services.grading_service import GradingService
from services.sandbox_runner import SandboxedRunner

router = APIRouter(prefix="/api/student", tags=["Student"])
grading_service = GradingService()
sandbox_runner = SandboxedRunner()


# Helper: Extrahiere PublicTest-Klasse aus test_code-String
def extract_public_tests(test_code: str) -> str:
    """
    Parse PublicTest class from test_code string.
    Returns just the PublicTest class code.
    """
    if not test_code:
        return ""
    # Find class PublicTest ... up to next class definition or end
    match = re.search(r'class PublicTest\(unittest\.TestCase\):([\s\S]*?)(?=class PrivateTest|$)', test_code)
    if match:
        return 'class PublicTest(unittest.TestCase):' + match.group(1).rstrip()
    return ""


# Helper: Filtere nur Public-Test-Resultate
def filter_public_test_results(test_results: list) -> list:
    """
    Filtert Test-Ergebnisse auf PublicTest-Klasse.
    Ein Test ist public wenn der Name 'PublicTest' enthaelt.
    """
    return [t for t in test_results if 'PublicTest' in t.get('name', '')]


# Helper: Formatiere Test-Ausgabe fuer Studierende
def format_test_error(output: str) -> str:
    """
    Wandelt den rohen Traceback in eine hilfreichere Meldung um.
    Extrahiert nur die relevanten Zeilen (Fehlermeldung + Zeilennummer).
    """
    if not output:
        return "Test fehlgeschlagen. Details unbekannt."
    
    # Extrahiere die eigentliche Fehlermeldung (AssertionError, etc.)
    lines = output.strip().split('\n')
    
    # Suche nach der eigentlichen Exception
    error_msg = ""
    expected_line = ""
    actual_line = ""
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if 'AssertionError' in stripped:
            error_msg = stripped
            # Hole die vorherige Zeile als Kontext
            if i > 0:
                expected_line = lines[i-1].strip()
        if 'assertEqual' in stripped and 'expected' in stripped.lower():
            expected_line = stripped
    
    # Wenn wir eine gute Fehlermeldung haben
    if error_msg and expected_line:
        return f"Test fehlgeschlagen: {error_msg} (Kontext: {expected_line[:80]}...)"
    
    # Fallback: Zeige nur die letzten 3 Zeilen des Tracebacks
    if len(lines) > 3:
        relevant = lines[-3:]
        return " | ".join(r.strip() for r in relevant if r.strip())
    
    return output.strip()[:200]


# =================================================================
# AUFGABEN
# =================================================================

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

    public_test_code = extract_public_tests(task.test_code or "")

    return {
        "id": task.id,
        "title": task.title,
        "task_type": task.task_type.value,
        "description": task.description,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "deadline": task.deadline,
        "code_template": task.code_template if task.task_type.value == "code" else None,
        "public_test_code": public_test_code,
    }


# =================================================================
# EINREICHUNGEN
# =================================================================

@router.post("/tasks/{task_id}/submit")
async def submit_solution(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Loesung einreichen -> sofort speichern, Grading im Hintergrund.

    Request (je nach Typ):
        Text: { "solution": "..." }
        Code: { "code_solution": "..." }

    Gibt sofort "pending" zurueck. Frontend muss per
    GET /submissions/{id}/result das Ergebnis polling-abfragen.
    """
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    # Check course access
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()

    if not membership or membership.role_in_course != CourseRole.STUDENT:
        raise HTTPException(403, "Kein Zugriff auf diese Aufgabe.")

    body = await request.json()

    # Deadline check
    if task.deadline:
        dl_str = task.deadline.replace("Z", "+00:00")
        deadline = datetime.fromisoformat(dl_str)
        # Ensure both datetimes are timezone-aware
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > deadline:
            raise HTTPException(400, "Deadline ist verstrichen.")

    # Max attempts check
    existing_subs = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == user.id)
    ).all()
    if task.max_attempts and len(existing_subs) >= task.max_attempts:
        raise HTTPException(400, f"Maximal {task.max_attempts} Versuche erlaubt.")

    # Type narrowing: session.get() gibt immer ein Objekt mit ID zurueck
    assert task.id is not None
    assert user.id is not None

    # Create submission (status bleibt PENDING)
    submission = Submission(
        task_id=task.id,
        student_id=user.id,
        solution=body.get("solution", ""),
        code_solution=body.get("code_solution", ""),
        attempt_number=len(existing_subs) + 1,
        status=SubmissionStatus.PENDING,
    )

    session.add(submission)
    session.commit()
    session.refresh(submission)

    # Type narrowing: Nach refresh() ist die ID gesetzt
    assert submission.id is not None

    # Grading im Hintergrund starten (asyncio.create_task = waehrend Antwort-Generierung)
    # Der Task laeuft parallel im Event-Loop — blockiert andere Requests NICHT
    # (Sandbox selbst ist async und gibt den Event-Loop frei waehrend subprocess)
    _ = asyncio.create_task(
        _run_grading_background(
            task_id=task.id,
            submission_id=submission.id,
            attempt_number=submission.attempt_number,
        )
    )

    # Sofort zurueckgeben — Frontend pollt das Ergebnis
    return {
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "status": "pending",
        "message": "Loesung wird korrigiert... bitte warten.",
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
    }


# ──────────────────────────────────────────────────────────────
# BACKGROUND: Asynchrones Grading (Laeuft im Hintergrund)
# ──────────────────────────────────────────────────────────────

async def _run_grading_background(
    task_id: int,
    submission_id: int,
    attempt_number: int,
):
    """
    Fuehrt das Grading in einem eigenen asyncio-Task aus.
    Verwendet eine eigene DB-Session, um Konflikte mit anderen
    Requests zu vermeiden.
    """
    from database.base import engine
    try:
        with Session(engine) as bg_session:
            task = bg_session.get(Task, task_id)
            submission = bg_session.get(Submission, submission_id)
            if not task or not submission:
                return

            await grading_service.grade_submission(
                task, submission, bg_session,
            )
    except Exception as e:
        # Fehler: Status auf PENDING lassen + Fehler-Feedback speichern
        try:
            with Session(engine) as err_session:
                sub = err_session.get(Submission, submission_id)
                if sub and sub.id is not None:
                    sub.status = SubmissionStatus.PENDING
                    err_session.add(Feedback(
                        submission_id=sub.id,
                        source=FeedbackSource.LLM,
                        points_earned=0,
                        comment=f"Grading-Fehler: {str(e)}",
                    ))
                    err_session.commit()
        except Exception:
            pass  # Logging hier waere ideal, aber nicht kritisch


# ──────────────────────────────────────────────────────────────
# POLLING: Ergebnis abfragen
# ──────────────────────────────────────────────────────────────

@router.get("/submissions/{submission_id}/result")
async def get_submission_result(
    submission_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Polling-Endpoint: Prueft Status und gibt Ergebnis zurueck,
    sobald das Grading abgeschlossen ist.

    Rueckgabe:
        pending:  { "status": "pending", "message": "..." }
        graded:   Vollstaendiges Ergebnis mit Punkten, Feedback, etc.
    """
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(404, "Einreichung nicht gefunden.")

    # Zugriffskontrolle
    if submission.student_id != user.id:
        raise HTTPException(403, "Kein Zugriff.")

    task = session.get(Task, submission.task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    if submission.status.value == "pending":
        # Prüfe ob es ein Fehler-Feedback gibt
        has_error_feedback = False
        for fb in submission.feedback_list:
            if fb.comment.startswith("Grading-Fehler:"):
                has_error_feedback = True
                break

        if has_error_feedback:
            # Liefere den Fehler
            for fb in submission.feedback_list:
                if fb.comment.startswith("Grading-Fehler:"):
                    return {
                        "status": "error",
                        "submission_id": submission.id,
                        "attempt_number": submission.attempt_number,
                        "message": fb.comment,
                        "points": 0,
                        "comment": fb.comment,
                        "max_points": task.max_points,
                    }

        return {
            "status": "pending",
            "submission_id": submission.id,
            "attempt_number": submission.attempt_number,
            "message": "Loesung wird korrigiert... bitte warten.",
        }

    # Graded (oder overridden) — Ergebnis zusammenbauen
    all_feedback = submission.feedback_list

    # Find best points
    existing = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == user.id)
    ).all()
    total_attempts = len(existing)

    best_points = 0.0
    for sub in existing:
        for fb in sub.feedback_list:
            if fb.points_earned > best_points:
                best_points = fb.points_earned

    points = 0.0
    comment = ""
    if all_feedback:
        # Take the most recent LLM feedback
        latest = max(all_feedback, key=lambda f: f.created_at)
        points = latest.points_earned
        comment = latest.comment

    if points > best_points:
        best_points = points

    response = {
        "status": submission.status.value,
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "total_attempts": total_attempts,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else "",
        "points": points,
        "best_points": best_points,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "comment": comment,
    }

    # Remaining attempts
    if task.max_attempts:
        response["remaining_attempts"] = task.max_attempts - total_attempts

    return response


# =================================================================
# CODE-AUSFUEHRUNG (ohne Tests, nur stdout)
# =================================================================

@router.post("/tasks/{task_id}/run-code")
async def run_code(
    task_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Code ausfuehren ohne Unit-Tests — zeigt nur die Konsolen-Ausgabe.
    Laeuft mit Timeout (sandbox config) — blockiert den Server NICHT
    fueher als SANDBOX_TIMEOUT Sekunden.
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

    body = await request.json()
    code = body.get("code_solution", "")

    if not code.strip():
        raise HTTPException(400, "Kein Code eingereicht.")

    # Fuehre Code in Sandbox aus (async, mit Timeout)
    result = await sandbox_runner.run_code_only(code=code)

    stdout = result.get("stdout", "").strip()
    stderr = result.get("stderr", "").strip()

    if stderr:
        # Uebersetze haeufige Fehler fuer Studierende
        if "IndentationError" in stderr:
            # Extrahiere die Zeilennummer
            match = re.search(r'line\s+(\d+)', stderr)
            line = match.group(1) if match else "unbekannt"
            stderr = f"Eindeckungsfehler (Indentation) in Zeile {line}. Ueberpruefe deine Einrueckung (Tab vs. Leerzeichen)."

    images = result.get("images", [])

    return {
        "stdout": stdout,
        "stderr": stderr,
        "error": stderr or None,
        "timeout": bool(stderr and "Zeitlimit" in stderr),
        "images": images,
    }


# =================================================================
# PUBLIC TESTS (schnelles Feedback, kein Grading)
# =================================================================

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
    Laeuft mit Timeout — blockiert den Server NICHT fueher als SANDBOX_TIMEOUT.
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

    public_test_code = extract_public_tests(task.test_code or "")
    if not public_test_code:
        return {"message": "Keine Public Tests fuer diese Aufgabe.", "test_results": []}

    body = await request.json()
    code = body.get("code_solution", "")

    if not code.strip():
        raise HTTPException(400, "Kein Code eingereicht.")

    result = await sandbox_runner.run(code=code, tests_code=public_test_code)

    # Formatte Fehlermeldungen fuer bessere Lesbarkeit
    formatted_results = []
    for t in result.get("test_results", []):
        formatted = {
            "name": t.get("name", "Unbekannt"),
            "passed": t.get("passed", False),
        }
        if not formatted["passed"]:
            raw_output = t.get("output", "")
            formatted["error"] = format_test_error(raw_output)
        formatted_results.append(formatted)

    has_timeout = result.get("timeout", False)
    stderr_val = result.get("stderr", "")
    if "Zeitlimit" in stderr_val:
        has_timeout = True

    return {
        "passed": result.get("passed", False),
        "test_results": formatted_results,
        "tests_passed": sum(1 for t in formatted_results if t.get("passed")),
        "tests_total": len(formatted_results),
        "stderr": stderr_val,
        "timeout": has_timeout,
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
    """Alle eigenen Einreichungen im Kurs."""
    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Kurs-Mitglied.")

    tasks = session.exec(
        select(Task).where(Task.course_id == course_id)
    ).all()

    my_submissions = []
    for task in tasks:
        subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
            .order_by(Submission.submitted_at.desc())
        ).all()

        if not subs:
            continue

        best_points = 0.0
        best_sub = None
        for sub in subs:
            for fb in sub.feedback_list:
                if fb.points_earned > best_points:
                    best_points = fb.points_earned
                    best_sub = sub

        my_submissions.append({
            "task_id": task.id,
            "task_title": task.title,
            "max_points": task.max_points,
            "best_points": best_points,
            "attempt_count": len(subs),
        })

    return {
        "course_id": course_id,
        "submissions": my_submissions,
    }


@router.get("/courses/{course_id}/my-points")
async def get_my_points(
    course_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Punkteübersicht für alle Aufgaben im Kurs."""
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

    total_points = 0.0
    max_points = 0
    for task in tasks:
        max_points += task.max_points
        subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == user.id)
        ).all()

        best_points = 0.0
        for sub in subs:
            for fb in sub.feedback_list:
                if fb.points_earned > best_points:
                    best_points = fb.points_earned
        total_points += best_points

    return {
        "total_points": round(total_points, 1),
        "max_points": max_points,
        "percentage": round(total_points / max_points * 100, 1) if max_points else 0,
    }