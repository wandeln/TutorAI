"""
Grading-Service: Koordination zwischen Sandbox, LLM und Feedback-Speicherung.

Orchestriert den kompletten Korrektur-Workflow:
1. Code-Aufgaben -> Sandbox ausfuehren (public + private Tests)
2. LLM korrigiert (Text: direkt, Code: mit Test-Ergebnissen)
3. Feedback + Punkte in DB speichern
4. Ergebnis an Frontend zurueckgeben
"""

import json
import re
from typing import Optional
from sqlmodel import Session, select

from services.settings_resolver import get_effective_llm_config
from database.models import (
    Task, Submission, Feedback, FeedbackSource,
    SubmissionStatus,
)
from services.llm_service import LLMService
from services.sandbox_runner import SandboxedRunner


class GradingService:
    """
    Koordiniert den kompletten Korrektur-Workflow.

    Usage:
        grading = GradingService()
        result = await grading.grade_submission(task, submission, session)
    """

    def __init__(self):
        self.llm = LLMService()
        self.sandbox = SandboxedRunner()

    def get_llm_config(self, task: Task, session: Session) -> dict:
        """Liest LLM-Config via Resolver (course > global > .env > default)."""
        cfg = get_effective_llm_config(session, task.course_id)
        return {
            "api_url": cfg["api_url"],
            "api_key": cfg["api_key"],
            "model": cfg["model"],
            "grading_prompt": cfg["grading_prompt"],
        }

    async def grade_submission(
        self,
        task: Task,
        submission: Submission,
        session: Session,
        custom_prompt: Optional[str] = None,
    ) -> dict:
        """
        Korrigiert eine Einreichung (Text oder Code).

        Returns:
            {
                "points": float,
                "max_points": int,
                "feedback": Feedback,   # Feedback-Objekt (DB)
                "comment": str,         # Der Feedback-Text als String (fuer Frontend)
                "test_results": list (nur bei Code),
                "tests_passed": int (nur bei Code),
                "tests_total": int (nur bei Code),
            }
        """
        if task.task_type.value == "code":
            result = await self._grade_code(task, submission, session, custom_prompt)
        else:
            result = await self._grade_text(task, submission, session, custom_prompt)

        # Status updaten
        submission.status = SubmissionStatus.GRADED
        session.add(submission)
        session.commit()
        session.refresh(submission)

        return result

    async def _grade_text(
        self, task: Task, submission: Submission, session: Session,
        custom_prompt: Optional[str] = None,
    ) -> dict:
        """LLM korrigiert Textaufgabe."""
        llm_cfg = self.get_llm_config(task, session)
        llm_result = await self.llm.grade_text_task(
            task_description=task.description,
            model_solution=task.model_solution or "(Keine Musterloesung hinterlegt — bitte eigenstaendig bewerten)",
            student_solution=submission.solution,
            max_points=task.max_points,
            custom_prompt=custom_prompt,
            config=llm_cfg,
        )

        data = llm_result.get("data", {})
        points = float(data.get("points", 0))
        comment = data.get("feedback", "Kein Feedback generiert.")

        feedback = Feedback(
            submission_id=submission.id,
            source=FeedbackSource.LLM,
            points_earned=points,
            comment=comment,
        )
        session.add(feedback)
        session.commit()
        session.refresh(feedback)

        return {
            "points": points,
            "max_points": task.max_points,
            "feedback": feedback,
            "comment": comment,
        }

    async def _grade_text_with_code(
        self, task: Task, submission: Submission, session: Session,
        custom_prompt: Optional[str] = None,
    ) -> dict:
        """Code-Aufgabe ohne Tests -> LLM korrigiert Code als Textloesung."""
        llm_cfg = self.get_llm_config(task, session)
        llm_result = await self.llm.grade_text_task(
            task_description=task.description,
            model_solution=task.model_solution or "(Keine Musterloesung hinterlegt — bitte eigenstaendig bewerten)",
            student_solution=submission.code_solution,  # <-- Code statt solution!
            max_points=task.max_points,
            custom_prompt=custom_prompt,
            config=llm_cfg,
        )

        data = llm_result.get("data", {})
        points = float(data.get("points", 0))
        comment = data.get("feedback", "Kein Feedback generiert.")

        feedback = Feedback(
            submission_id=submission.id,
            source=FeedbackSource.LLM,
            points_earned=points,
            comment=comment,
        )
        session.add(feedback)
        session.commit()
        session.refresh(feedback)

        return {
            "points": points,
            "max_points": task.max_points,
            "feedback": feedback,
            "comment": comment,
        }

    async def _grade_code(
        self, task: Task, submission: Submission, session: Session,
        custom_prompt: Optional[str] = None,
    ) -> dict:
        """
        Code-Aufgabe: 1) Sandbox-Tests ausfuehren, 2) LLM korrigiert.
        """
        # 1. Tests laden (einziger test_code String)
        test_code = task.test_code or ""

        if not test_code.strip():
            # Keine Tests -> Code als Textloesung graded
            return await self._grade_text_with_code(task, submission, session, custom_prompt)

        # 2. Sandbox ausfuehren (ganzer test_code, public + private)
        sandbox_result = await self.sandbox.run(
            code=submission.code_solution,
            tests_code=test_code,
        )

        # 3. Test-Ergebnis formatieren fuer LLM
        test_summary = self._format_test_results(sandbox_result)

        # 4. LLM korrigiert (mit Test-Ergebnissen)
        llm_cfg = self.get_llm_config(task, session)
        llm_result = await self.llm.grade_code_task(
            task_description=task.description,
            model_solution=task.model_solution or "(Keine Musterloesung hinterlegt — Tests sind die Referenz)",
            student_code=submission.code_solution,
            test_results=test_summary,
            max_points=task.max_points,
            code_template=task.code_template,
            custom_prompt=custom_prompt,
            config=llm_cfg,
        )

        data = llm_result.get("data", {})
        points = float(data.get("points", 0))
        comment = data.get("feedback", "Kein Feedback generiert.")

        feedback = Feedback(
            submission_id=submission.id,
            source=FeedbackSource.LLM,
            points_earned=points,
            comment=comment,
        )
        session.add(feedback)
        session.commit()
        session.refresh(feedback)

        test_results = sandbox_result.get("test_results", [])
        return {
            "points": points,
            "max_points": task.max_points,
            "feedback": feedback,
            "comment": comment,
            "test_results": test_results,
            "tests_passed": sum(1 for t in test_results if t.get("passed")),
            "tests_total": len(test_results),
        }

    def _format_test_results(self, sandbox_result: dict) -> str:
        """Formatiert Sandbox-Output als lesbaren Text fuer den LLM."""
        test_results = sandbox_result.get("test_results", [])
        lines = []
        for i, result in enumerate(test_results):
            passed = result.get("passed", False)
            status = "✅ PASSED" if passed else "❌ FAILED"
            name = result.get("name", f"Test {i+1}")
            lines.append(f"Test {i+1} ({name}): {status}")
            if not passed and result.get("output"):
                lines.append(f"  Fehler: {result['output'][:200]}")

        if sandbox_result.get("timeout"):
            lines.append("⏰ TIMEOUT: Zeitlimit ueberschritten")
        elif sandbox_result.get("stderr"):
            lines.append(f"⚠️ Runtime Error: {sandbox_result['stderr'][:200]}")

        total = len(test_results)
        passed = sum(1 for t in test_results if t.get("passed"))
        lines.insert(0, f"Ergebnis: {passed}/{total} Tests bestanden")

        return "\n".join(lines)