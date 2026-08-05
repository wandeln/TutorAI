"""
Student-Endpoints: Aufgaben ansehen, Lösungen einreichen, Tests ausführen.

Rolle: Student (im Kurs)
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, SQLModel, select

from database.base import get_session
from database.models import (
    User, Task, Submission, Feedback, HintExchange,
    TaskType, SubmissionStatus, FeedbackSource,
    Course, UserCourse, CourseRole,
)
from services.auth_service import get_current_user
from services.grading_service import GradingService
from services.llm_service import LLMService
from services.sandbox_runner import SandboxedRunner
from services.settings_resolver import get_effective_llm_config

router = APIRouter(prefix="/api/student", tags=["Student"])
grading_service = GradingService()
sandbox_runner = SandboxedRunner()
llm_service = LLMService()


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
def format_test_error(output: str) -> dict:
    """
    Bereitet die Fehlermeldung eines Tests auf.
    Bewahrt Assert-Messages und den vollen Traceback auf.
    Gibt ein Dict zurueck mit 'summary' (lesbare Kurzform) und 'full' (kompletter Traceback).
    """
    if not output:
        return {"summary": "Test fehlgeschlagen. Details unbekannt.", "full": ""}

    lines = output.strip().split('\n')

    # Suche nach der Exception-Zeile und ggf. Assert-Message
    error_type = ""
    error_msg = ""
    found_exception = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not found_exception:
            # Suche nach Exception-Klasse (z.B. AssertionError, ValueError, ...)
            for exc in ('AssertionError', 'ValueError', 'TypeError', 'KeyError', 'IndexError', 'NameError', 'AttributeError', 'RuntimeError', 'ZeroDivisionError'):
                if exc in stripped and '(' in stripped:
                    found_exception = True
                    error_type = exc
                    # Extrahiere die Message aus der Exception-Zeile
                    paren_idx = stripped.index('(')
                    msg_part = stripped[paren_idx+1:].rstrip(')').strip().strip("'\"")
                    error_msg = msg_part
                    break
        else:
            break

    # Baue eine gut lesbare Zusammenfassung
    readable_parts = []
    if error_type:
        readable_parts.append(f"{error_type}")
        if error_msg:
            readable_parts[-1] += f": {error_msg}"

    # Wenn die Exception-Meldung leer war (z.B. AssertionError ohne Message),
    # verwende die erste signifikante Zeile des Tracebacks
    if error_type and not error_msg:
        for l in lines:
            stripped = l.strip()
            if stripped and stripped != error_type:
                readable_parts[-1] += f": {stripped[:120]}"
                break

    readable = " — ".join(readable_parts) if readable_parts else output.strip()[:300]

    return {"summary": readable, "full": output.strip()}


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
        "hints_enabled": task.hints_enabled,
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

    # Find latest points (from most recent submission)
    existing = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == user.id)
        .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
    ).all()
    total_attempts = len(existing)

    # Latest points = Punktzahl der neuesten Einreichung
    latest_points = 0.0
    if existing:
        latest_sub = existing[0]  # newest
        human_points = 0.0
        llm_points = 0.0
        override_exists = False
        for fb in latest_sub.feedback_list:
            if fb.source == FeedbackSource.HUMAN:
                human_points = max(human_points, fb.points_earned)
                override_exists = True
            else:
                llm_points = max(llm_points, fb.points_earned)
        latest_points = human_points if override_exists else llm_points

    points = 0.0
    comment = ""
    if all_feedback:
        # Take the most recent LLM feedback
        latest = max(all_feedback, key=lambda f: f.created_at)
        points = latest.points_earned
        comment = latest.comment

    response = {
        "status": submission.status.value,
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "total_attempts": total_attempts,
        "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else "",
        "points": points,
        "latest_points": latest_points,
        "max_points": task.max_points,
        "max_attempts": task.max_attempts,
        "comment": comment,
    }

    # Remaining attempts
    if task.max_attempts:
        response["remaining_attempts"] = task.max_attempts - total_attempts

    # Gruppen-Durchschnitt: Alle Kursstudenten zählen (ohne Abgabe = 0 Punkte)
    course_students = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == task.course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()
    group_scores = []
    for gm in course_students:
        gm_subs = session.exec(
            select(Submission)
            .where(Submission.task_id == task.id)
            .where(Submission.student_id == gm.user_id)
            .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
        ).all()
        if gm_subs:
            gm_latest = gm_subs[0]
            gm_human = 0.0
            gm_llm = 0.0
            gm_override = False
            for fb in gm_latest.feedback_list:
                if fb.source == FeedbackSource.HUMAN:
                    gm_human = max(gm_human, fb.points_earned)
                    gm_override = True
                else:
                    gm_llm = max(gm_llm, fb.points_earned)
            group_scores.append(gm_human if gm_override else gm_llm)
        else:
            group_scores.append(0.0)
    task_group_avg = round(sum(group_scores) / len(group_scores), 1) if group_scores else 0.0

    response["task_group_avg"] = task_group_avg

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

    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    # Behalte den vollen Python-Traceback anstelle ihn zu ersetzen
    if stderr:
        if "IndentationError" in stderr:
            match = re.search(r'line\s+(\d+)', stderr)
            line = match.group(1) if match else "unbekannt"
            stderr = f"Eindeckungsfehler (Indentation) in Zeile {line}. Ueberpruefe deine Einrueckung (Tab vs. Leerzeichen).\n\nOriginal:\n{stderr}"

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
            formatted_error = format_test_error(raw_output)
            formatted["error"] = formatted_error
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
        "stdout": result.get("stdout", ""),
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

        latest_sub = subs[0]  # newest (already ordered desc)
        human_points = 0.0
        llm_points = 0.0
        override_exists = False
        for fb in latest_sub.feedback_list:
            if fb.source == FeedbackSource.HUMAN:
                human_points = max(human_points, fb.points_earned)
                override_exists = True
            else:
                llm_points = max(llm_points, fb.points_earned)
        latest_points = human_points if override_exists else llm_points


        my_submissions.append({
            "task_id": task.id,
            "task_title": task.title,
            "max_points": task.max_points,
            "latest_points": latest_points,
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
            .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
        ).all()

        latest_points = 0.0
        if subs:
            latest_sub = subs[0]  # newest
            human_points = 0.0
            llm_points = 0.0
            override_exists = False
            for fb in latest_sub.feedback_list:
                if fb.source == FeedbackSource.HUMAN:
                    human_points = max(human_points, fb.points_earned)
                    override_exists = True
                else:
                    llm_points = max(llm_points, fb.points_earned)
            latest_points = human_points if override_exists else llm_points

        total_points += latest_points

    # Berechne Perzentil im Kurs: Gesamtpunkte aller Studenten vergleichen
    student_members = session.exec(
        select(UserCourse)
        .where(UserCourse.course_id == course_id)
        .where(UserCourse.role_in_course == CourseRole.STUDENT)
    ).all()

    other_total_scores = []
    for member in student_members:
        if member.user_id == user.id:
            continue
        member_total = 0.0
        for task in tasks:
            m_subs = session.exec(
                select(Submission)
                .where(Submission.task_id == task.id)
                .where(Submission.student_id == member.user_id)
                .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
            ).all()
            if m_subs:
                m_latest = m_subs[0]
                m_human = 0.0
                m_llm = 0.0
                m_override = False
                for fb in m_latest.feedback_list:
                    if fb.source == FeedbackSource.HUMAN:
                        m_human = max(m_human, fb.points_earned)
                        m_override = True
                    else:
                        m_llm = max(m_llm, fb.points_earned)
                member_total += m_human if m_override else m_llm
        other_total_scores.append(member_total)

    below = sum(1 for s in other_total_scores if s < total_points)
    equal = sum(1 for s in other_total_scores if s == total_points)
    if other_total_scores:
        course_percentile = round((below + 0.5 * equal) / len(other_total_scores) * 100)
    else:
        course_percentile = 100

    # Gruppen-Durchschnitt: Alle Kursstudenten zählen (ohne Abgabe = 0 Punkte)
    all_scores = other_total_scores + [total_points]
    group_avg = round(sum(all_scores) / len(all_scores), 1)

    return {
        "total_points": round(total_points, 1),
        "max_points": max_points,
        "percentage": round(total_points / max_points * 100, 1) if max_points else 0,
        "course_percentile": course_percentile,
        "group_avg": group_avg,
    }


# =================================================================
# FOTO -> LATEX KONVERTIERUNG
# =================================================================

@router.post("/latex-from-image")
async def latex_from_image(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Konvertiert ein Foto (Base64) einer handgeschriebenen Notiz mit Formeln
    in Markdown mit LaTeX-Code via LLM.
    """
    body = await request.json()
    image_base64 = body.get("image_base64", "")
    mime_type = body.get("mime_type", "image/png")

    if not image_base64:
        raise HTTPException(400, "Kein Bild uebergeben.")

    # Resolve effektive LLM-Config (wird vom LLM-Service genutzt)
    llm_cfg = get_effective_llm_config(session)

    result = await llm_service.convert_image_to_latex(
        image_base64=image_base64,
        mime_type=mime_type,
        config=llm_cfg,
    )

    if not result.get("success"):
        raise HTTPException(500, f"LLM-Fehler: {result.get('error', 'Unbekannter Fehler')}")

    return {
        "latex": result.get("data", {}).get("latex", ""),
        "latency_ms": result.get("latency_ms", 0),
    }


# =================================================================
# SOKRATISCHE HINWEISE (Hints)
# =================================================================

class HintRequest(SQLModel):
    question: str
    current_solution: str = ""


@router.post("/tasks/{task_id}/hints")
async def request_hint(
    task_id: int,
    hint_request: HintRequest,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Student fragt einen sokratischen Hinweis bei.

    Das LLM bekommt Kontext (Aufgabenstellung, Musterloesung, aktuelle Loesung,
    vorherige Submissions mit Feedback, Hinweisverlauf) und antwortet in
    sokratischer Weise, ohne die Loesung zu verraten.
    """
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Aufgabe nicht gefunden.")

    if not task.hints_enabled:
        raise HTTPException(403, "Hinweise sind fuer diese Aufgabe deaktiviert.")

    membership = session.exec(
        select(UserCourse)
        .where(UserCourse.user_id == user.id)
        .where(UserCourse.course_id == task.course_id)
    ).first()

    if not membership:
        raise HTTPException(403, "Kein Zugriff auf diese Aufgabe.")

    # Sammle Hinweise-Verlauf (letzte 5) fuer Kontext
    hint_history = session.exec(
        select(HintExchange)
        .where(HintExchange.task_id == task.id)
        .where(HintExchange.student_id == user.id)
        .order_by(HintExchange.created_at.desc())  # type: ignore[attr-defined]
    ).all()

    hint_history_text = ""
    if hint_history:
        lines = []
        for i, he in enumerate(hint_history[:5]):
            lines.append(f"Frage {i+1}: {he.question}")
            lines.append(f"Antwort {i+1}: {he.llm_response}")
            lines.append("")
        hint_history_text = "\n".join(lines)

    # Sammle vorherige Submissions mit Feedback
    prev_submissions = session.exec(
        select(Submission)
        .where(Submission.task_id == task.id)
        .where(Submission.student_id == user.id)
        .order_by(Submission.submitted_at.desc())  # type: ignore[attr-defined]
    ).all()

    prev_submissions_text = ""
    if prev_submissions:
        lines = []
        for sub in prev_submissions[:5]:
            solution_text = sub.solution if task.task_type.value == "text" else sub.code_solution
            lines.append(f"Abgabe #{sub.attempt_number}: {solution_text[:500]}")
            for fb in sub.feedback_list:
                lines.append(f"  Feedback: {fb.comment}")
                lines.append(f"  Punkte: {fb.points_earned}/{task.max_points}")
            lines.append("")
        prev_submissions_text = "\n".join(lines)

    # Erstelle HintExchange-Eintrag
    hint_exchange = HintExchange(
        task_id=task.id,
        student_id=user.id,
        question=hint_request.question,
        current_solution=hint_request.current_solution,
    )
    session.add(hint_exchange)
    session.commit()
    session.refresh(hint_exchange)

    # Resolve effektive LLM-Config
    llm_cfg = get_effective_llm_config(session)

    # Rufe LLM fuer sokratischen Hinweis auf
    result = await llm_service.generate_socratic_hint(
        task_description=task.description,
        model_solution=task.model_solution or "(Keine Musterloesung hintergelegt)",
        code_template=task.code_template or "",
        current_solution=hint_request.current_solution,
        previous_submissions=prev_submissions_text,
        hint_history=hint_history_text,
        student_question=hint_request.question,
        config=llm_cfg,
    )

    if not result.get("success"):
        error_msg = result.get("error", "Unbekannter Fehler")
        hint_exchange.llm_response = f"Fehler beim Generieren des Hinweises: {error_msg}"
        session.commit()
        raise HTTPException(500, f"LLM-Fehler: {error_msg}")

    # Speichere LLM-Antwort
    llm_data = result.get("data", {})
    hint_text = llm_data.get("hint", "Konnte keinen Hinweis generieren.")
    hint_exchange.llm_response = hint_text
    hint_exchange.response_at = datetime.now()
    session.commit()
    session.refresh(hint_exchange)

    return {
        "id": hint_exchange.id,
        "question": hint_exchange.question,
        "llm_response": hint_text,
        "suggestion_type": llm_data.get("suggestion_type", "hint"),
        "created_at": hint_exchange.created_at.isoformat(),
        "response_at": hint_exchange.response_at.isoformat() if hint_exchange.response_at else None,
        "latency_ms": result.get("latency_ms", 0),
    }


@router.get("/tasks/{task_id}/hints")
async def get_hints(
    task_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Lädt den Hinweis-Verlauf fuer eine Aufgabe."""
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

    hints = session.exec(
        select(HintExchange)
        .where(HintExchange.task_id == task.id)
        .where(HintExchange.student_id == user.id)
        .order_by(HintExchange.created_at.asc())  # type: ignore[attr-defined]
    ).all()

    return [
        {
            "id": h.id,
            "question": h.question,
            "llm_response": h.llm_response,
            "current_solution": h.current_solution,
            "created_at": h.created_at.isoformat(),
            "response_at": h.response_at.isoformat() if h.response_at else None,
        }
        for h in hints
    ]
