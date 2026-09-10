"""
LLM-Service: Kommunikation mit OpenAI-kompatiblen Endpoints.

Unterstützt:
- Qwen3, Llama, Mistral, etc. (jedes OpenAI-kompatible Modell)
- Grading (Korrektur) mit JSON-Response
- Task-Generierung (Aufgaben neu erstellen/ändern, Musterlösung, Code-Templates)
- Config pro Kurs (URL, Modell, Prompt)
"""

import asyncio
import inspect
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Any

from jinja2 import Template
from openai import AsyncOpenAI
from sqlmodel import Session, select

from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT
from database.base import engine
from database.models import LLMDebugEntry
from prompts.grading_prompt import GRADING_TEXT_PROMPT_TEMPLATE, GRADING_CODE_PROMPT_TEMPLATE
from prompts.creation_prompt import UNIFIED_TASK_PROMPT_TEMPLATE
from prompts.script_prompt import SCRIPT_SECTION_PROMPT_TEMPLATE
from prompts.slides_prompt import SLIDES_PROMPT_TEMPLATE
from prompts.markdown_manual import (
    SCRIPT_MARKDOWN_MANUAL,
    SLIDES_MARKDOWN_MANUAL,
    SCRIPT_CONTENT_EDITS_SPEC,
    SLIDES_CONTENT_EDITS_SPEC,
)
from prompts.solution_prompt import CODE_TEMPLATE_TESTS_PROMPT_TEMPLATE
from prompts.hint_prompt import SOCRATIC_HINT_PROMPT_TEMPLATE
from prompts.script_question_prompt import SCRIPT_QUESTION_PROMPT_TEMPLATE
from prompts.report_prompt import COURSE_REPORT_PROMPT_TEMPLATE, STUDENT_REPORT_PROMPT_TEMPLATE
from prompts.applet_prompt import APPLET_PROMPT_TEMPLATE
from prompts.import_prompt import (
    FILE_SUMMARY_PROMPT_TEMPLATE,
    SCRIPT_PLANNER_PROMPT_TEMPLATE,
    SLIDES_PLANNER_PROMPT_TEMPLATE,
    CONVERT_CHAPTER_PROMPT_TEMPLATE,
    CHAPTER_SUMMARY_PROMPT_TEMPLATE,
    SLIDE_DECK_PROMPT_TEMPLATE,
    GATHER_PROMPT_TEMPLATE,
    EXISTING_DESC_PROMPT_TEMPLATE,
    REF_EXTRACT_PROMPT_TEMPLATE,
    REFINE_CHAPTER_PROMPT_TEMPLATE,
    REFINE_SLIDE_DECK_PROMPT_TEMPLATE,
    DECK_SUMMARY_PROMPT_TEMPLATE,
    SCRIPT_FORMAT_SPEC,
    SLIDE_FORMAT_SPEC,
)

logger = logging.getLogger(__name__)

# Report-Timeout ist höher, da der Prompt sehr groß sein kann
REPORT_TIMEOUT = int(os.getenv("REPORT_TIMEOUT", "180"))
# Applet-Generierung: komplettes HTML-Dokument → längeres Budget + mehr Tokens
APPLET_TIMEOUT = int(os.getenv("APPLET_TIMEOUT", "240"))
APPLET_MAX_TOKENS = int(os.getenv("APPLET_MAX_TOKENS", "16384"))
# Slide-Deck-Generierung: Decks können lang sein (viele Folien + Sprechernotizen)
SLIDES_MAX_TOKENS = int(os.getenv("SLIDES_MAX_TOKENS", "16384"))


# ── LLM Debug Log (persistent, letzte 7 Tage) ────────────────────
# Protokolliert jeden LLM-Call für die Admin-Konsole (Tab "LLM-Debug-Log").
# Gespeichert in der Datenbank (Tabelle llm_debug_entries, überlebt
# Server-Restarts); alte Einträge werden automatisch purgt
# (TTL + Größen-/Eintragslimits).
_LLM_DEBUG_TTL = timedelta(days=7)
_LLM_DEBUG_MAX_FIELD = 100_000        # max. Zeichen je Textfeld
_LLM_DEBUG_MAX_TOTAL_CHARS = 5_000_000  # max. Gesamtgröße aller Einträge
_LLM_DEBUG_MAX_DB_ENTRIES = 500       # max. Einträge, die in der DB gespeichert werden
_LLM_DEBUG_MAX_ENTRIES = 200          # max. Einträge, die an die UI geliefert werden
_LLM_DEBUG_PURGE_INTERVAL = 3600      # Purge-Intervall in Sekunden

_llm_debug_lock = threading.Lock()
_llm_debug_last_purge = 0.0


def _debug_truncate(text: Optional[str]) -> str:
    """Begrenzt ein Log-Feld auf _LLM_DEBUG_MAX_FIELD Zeichen (Speicher-Schutz)."""
    if not text:
        return ""
    if len(text) <= _LLM_DEBUG_MAX_FIELD:
        return text
    return text[:_LLM_DEBUG_MAX_FIELD] + f"\n… [abgeschnitten: {len(text) - _LLM_DEBUG_MAX_FIELD} weitere Zeichen]"


def _debug_caller_label() -> str:
    """Bestimmt das Label aus dem Namen der aufrufenden Service-Methode.

    Geht die Call-Stack um die internen Helper (_call_plain/_call_with_json)
    herum, z. B. grade_text_task → GRADE_TEXT_TASK, import_refine_chapter →
    IMPORT_REFINE_CHAPTER. Damit alle Aufrufer automatisch geloggt werden.
    """
    internal = {"_debug_caller_label", "_call_plain", "_call_with_json"}
    try:
        frame = inspect.currentframe()
        frame = frame.f_back if frame else None
        while frame is not None:
            name = frame.f_code.co_name
            if name not in internal and not name.startswith("<"):
                return name.upper()
            frame = frame.f_back
    except Exception:
        pass
    return "UNKNOWN"


def _entry_chars(entry: LLMDebugEntry) -> int:
    """Gesamtgröße der Textfelder eines Log-Eintrags."""
    return sum(
        len(getattr(entry, key) or "")
        for key in ("system_prompt", "prompt", "response", "thinking", "error")
    )


def _purge_llm_debug_entries(session: Session) -> None:
    """Löscht abgelaufene Einträge und begrenzt Größe/Eintragszahl (älteste zuerst).

    Geht von den neuesten Einträgen zurück und behält so viele bei, wie die
    Limits zulassen; alles Ältere (inkl. älter als TTL) wird gelöscht.
    """
    rows = session.exec(select(LLMDebugEntry).order_by(LLMDebugEntry.ts.asc())).all()
    if not rows:
        return
    cutoff = datetime.now() - _LLM_DEBUG_TTL
    keep: list[LLMDebugEntry] = []
    total = 0
    for row in reversed(rows):  # neueste zuerst
        if row.ts < cutoff:
            break
        chars = _entry_chars(row)
        if total + chars > _LLM_DEBUG_MAX_TOTAL_CHARS:
            break
        if len(keep) >= _LLM_DEBUG_MAX_DB_ENTRIES:
            break
        total += chars
        keep.append(row)
    keep_ids = {row.id for row in keep}
    for row in rows:
        if row.id not in keep_ids:
            session.delete(row)
    session.commit()


def _maybe_purge_llm_debug() -> None:
    """Führt höchstens alle _LLM_DEBUG_PURGE_INTERVAL Sekunden einen Purge aus."""
    global _llm_debug_last_purge
    if time.monotonic() - _llm_debug_last_purge < _LLM_DEBUG_PURGE_INTERVAL:
        return
    _llm_debug_last_purge = time.monotonic()
    try:
        with Session(engine) as session:
            _purge_llm_debug_entries(session)
    except Exception as e:
        logger.warning(f"LLM-Debug-Log: Purge fehlgeschlagen: {e}")


def record_llm_debug_entry(
    label: str,
    model: str,
    url: str,
    is_public: bool,
    system_prompt: str,
    prompt: str,
    response: str,
    thinking: str = "",
    success: bool = True,
    error: str = "",
    latency_ms: int = 0,
    attempts: int = 1,
) -> None:
    """Hängt einen LLM-Call an das persistente Debug-Log an (lässt Aufrufer nie scheitern)."""
    try:
        with _llm_debug_lock:
            _maybe_purge_llm_debug()
            with Session(engine) as session:
                session.add(LLMDebugEntry(
                    ts=datetime.now(),
                    label=label,
                    model=model or "",
                    url=url or "",
                    is_public=bool(is_public),
                    system_prompt=_debug_truncate(system_prompt),
                    prompt=_debug_truncate(prompt),
                    response=_debug_truncate(response),
                    thinking=_debug_truncate(thinking),
                    success=bool(success),
                    error=_debug_truncate(error),
                    latency_ms=int(latency_ms or 0),
                    attempts=int(attempts or 1),
                ))
                session.commit()
    except Exception as e:
        logger.warning(f"LLM-Debug-Log: Eintrag konnte nicht gespeichert werden: {e}")


def get_llm_debug_log() -> list[dict]:
    """Gibt das Debug-Log (neueste zuerst, max. _LLM_DEBUG_MAX_ENTRIES Einträge) zurück."""
    try:
        cutoff = datetime.now() - _LLM_DEBUG_TTL
        with Session(engine) as session:
            rows = session.exec(
                select(LLMDebugEntry)
                .where(LLMDebugEntry.ts >= cutoff)
                .order_by(LLMDebugEntry.ts.desc(), LLMDebugEntry.id.desc())
                .limit(_LLM_DEBUG_MAX_ENTRIES)
            ).all()
        return [
            {
                "ts": row.ts.isoformat(),
                "label": row.label,
                "model": row.model,
                "url": row.url,
                "is_public": row.is_public,
                "system_prompt": row.system_prompt,
                "prompt": row.prompt,
                "response": row.response,
                "thinking": row.thinking,
                "success": row.success,
                "error": row.error,
                "latency_ms": row.latency_ms,
                "attempts": row.attempts,
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning(f"LLM-Debug-Log: Log konnte nicht geladen werden: {e}")
        return []


class LLMService:
    """
    Client für OpenAI-kompatible LLM-APIs.

    Usage:
        llm = LLMService()
        result = await llm.grade(task, student_solution)
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_url = api_url or LLM_API_URL
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL
        self.temperature = LLM_TEMPERATURE
        self.timeout = LLM_TIMEOUT

        self.client = AsyncOpenAI(
            base_url=self.api_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    @staticmethod
    def _render_prompt(template: str, **kwargs: Any) -> str:
        """Substituiert __PLACEHOLDER__-Muster in einem Prompt-Template.

        Verwendet .replace() statt str.format() oder Jinja2, damit geschweifte
        Klammern im Prompt-Text (z. B. JSON-Beispiele) keine Probleme bereiten.
        """
        result = template
        for key, value in kwargs.items():
            placeholder = f"__{key.upper()}__"
            result = result.replace(placeholder, str(value))
        return result

    # ── Public methods ───────────────────────────────────────────

    async def grade_text_task(
        self,
        task_description: str,
        model_solution: str,
        student_solution: str,
        max_points: int,
        custom_prompt: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        """Korrigiert eine Textaufgabe via LLM."""

        prompt = self._render_prompt(
            custom_prompt or GRADING_TEXT_PROMPT_TEMPLATE,
            task_description=task_description,
            model_solution=model_solution,
            student_solution=student_solution,
            max_points=max_points,
        )

        return await self._call_with_json(prompt, response_format={"type": "json_object"}, config=config)

    async def grade_code_task(
        self,
        task_description: str,
        model_solution: str,
        student_code: str,
        test_results: str,
        max_points: int,
        code_template: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        """Korrigiert eine Codeaufgabe via LLM (inkl. Test-Ergebnissen)."""

        prompt = self._render_prompt(
            custom_prompt or GRADING_CODE_PROMPT_TEMPLATE,
            task_description=task_description,
            model_solution=model_solution,
            code_template=code_template or "(Kein Template hintergelegt)",
            student_solution=student_code,
            test_results=test_results,
            max_points=max_points,
        )

        return await self._call_with_json(prompt, response_format={"type": "json_object"}, config=config)

    async def generate_task_fields(
        self,
        topic: str,
        difficulty: str,
        task_type: str,
        max_points: int,
        generate_fields: list[str],
        current_title: str = "",
        current_description: str = "",
        current_model_solution: str = "",
        code_template: str = "",
        script_chapters: Optional[list[dict]] = None,
        course_media: Optional[list[dict]] = None,
        config: Optional[dict] = None,
    ):
        """Generiert/ändert die angeforderten Felder einer Aufgabe via LLM.

        generate_fields: Untermenge von ["title", "description", "model_solution"].
        Das LLM liefert JSON mit EXAKT diesen Schlüsseln — nicht angeforderte
        Felder werden nicht zurückgegeben.

        Enthält keine sensitive Studentendaten — nutzt daher den Public
        Endpoint, falls konfiguriert.
        """
        task_type_description = {"text": "Textaufgabe", "code": "Codeaufgabe"}.get(task_type, task_type)
        generate_list = ", ".join(f'"{f}"' for f in generate_fields)

        prompt = Template(UNIFIED_TASK_PROMPT_TEMPLATE).render(
            task_type_description=task_type_description,
            topic=topic,
            difficulty=difficulty,
            max_points=max_points,
            generate_list=generate_list,
            current_title=current_title,
            current_description=current_description,
            current_model_solution=current_model_solution,
            code_template=code_template,
            script_chapters=script_chapters or [],
            course_media=course_media or [],
        )

        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=self._public_config(config)
        )

    async def generate_script_section(
        self,
        course_name: str,
        topic: str,
        generate_fields: list[str],
        current_title: str = "",
        current_content: str = "",
        other_chapters: Optional[list[dict]] = None,
        unused_media: Optional[list[dict]] = None,
        course_tasks: Optional[list[dict]] = None,
        config: Optional[dict] = None,
    ):
        """Generiert/ändert die angeforderten Felder eines Skript-Kapitels via LLM.

        topic: Vereinheitlichtes Feld aus der UI (Thema des Kapitels und/oder
        konkrete Änderungswünsche).
        generate_fields: Untermenge von ["title", "content", "summary"].
        other_chapters: [{title, summary}] der anderen Kapitel (Konsistenz).
        unused_media: [{title, description, url}] — noch nicht im Skript
        verwendete Medien (ggf. einbindbar).
        course_tasks: [{id, title}] — Übungsaufgaben des Kurses (ggf. per
        @task:{id} im Kapitel einbindbar → Aufgaben-Box für Studenten).
        Das LLM liefert JSON mit einer Untermenge der angeforderten Schlüssel —
        weggelassene Schlüssel = das Feld bleibt unverändert (leeres Feld im
        Response, Frontend behält den vorhandenen Wert). Für lokale Änderungen
        an vorhandenem Inhalt kann es „content_edits“ (Liste von Edit-Objekten)
        statt „content“ liefern — die Anwendung auf den bestehenden Inhalt
        erfolgt serverseitig (api.script._apply_content_edits).

        Enthält keine sensitive Studentendaten — nutzt daher den Public
        Endpoint, falls konfiguriert.
        """
        generate_list = ", ".join(f'"{f}"' for f in generate_fields)

        prompt = Template(SCRIPT_SECTION_PROMPT_TEMPLATE).render(
            course_name=course_name,
            topic=topic or "(Kein Thema angegeben — überarbeite das Kapitel sinnvoll.)",
            generate_list=generate_list,
            other_chapters=other_chapters or [],
            unused_media=unused_media or [],
            course_tasks=course_tasks or [],
            current_title=current_title,
            current_content=current_content,
            markdown_manual=SCRIPT_MARKDOWN_MANUAL,
            edits_spec=SCRIPT_CONTENT_EDITS_SPEC,
        )

        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=self._public_config(config)
        )

    async def generate_slide_deck(
        self,
        course_name: str,
        topic: str,
        minutes: int,
        generate_fields: list[str],
        current_title: str = "",
        current_content: str = "",
        chapters: Optional[list[dict]] = None,
        course_media: Optional[list[dict]] = None,
        course_tasks: Optional[list[dict]] = None,
        config: Optional[dict] = None,
    ):
        """Generiert/ändert ein Slide-Deck (Vorlesungsfolien) via LLM.

        topic: Vereinheitlichtes Feld aus der UI (Thema des Decks und/oder
        konkrete Änderungswünsche, z.B. „Folien zu Kapitel 3 erstellen“).
        minutes: Dauer der Präsentation in Minuten → das LLM plant die
        Folienanzahl entsprechend (Richtwert 1,5–2 min/Folie).
        generate_fields: Untermenge von ["title", "content"].
        chapters: [{title, labels, content}] der Skript-Kapitel (Inhalt als
        Folien-Basis; labels für Label-Konsistenz — gleiche Objekte dürfen
        dieselben Labels verwenden → gleiche Nummer + Link zum Skript).
        course_media: [{title, description, url}] — alle Medien des Kurses
        (ggf. einbinden; auch solche, die bereits im Skript vorkommen —
        Medien dürfen in Skript UND Slides verwendet werden).
        course_tasks: [{id, title}] — Übungsaufgaben des Kurses (ggf. per
        @task:{id} im Deck einbindbar → Aufgaben-Box).
        current_content: bestehendes Deck — für die Folien-Referenz in
        „content_edits“ mit expliziten Nummern („%% Folie N %%“) vorliegen
        (s. slides_service.numbered_slide_content); die Marker sind KEIN
        Deck-Inhalt und werden vom LLM nicht zurückgeliefert.
        Das LLM liefert JSON mit einer Untermenge der angeforderten Schlüssel —
        weggelassene Schlüssel = das Feld bleibt unverändert (leeres Feld im
        Response, Frontend behält den vorhandenen Wert). Für lokale Änderungen
        an vorhandenem Inhalt kann es „content_edits“ (Liste von Edit-Objekten)
        statt „content“ liefern — die Anwendung auf den bestehenden Inhalt
        erfolgt serverseitig (slides_service.apply_slide_edits).

        Enthält keine sensitive Studentendaten — nutzt daher den Public
        Endpoint, falls konfiguriert.
        """
        generate_list = ", ".join(f'"{f}"' for f in generate_fields)

        prompt = Template(SLIDES_PROMPT_TEMPLATE).render(
            course_name=course_name,
            topic=topic or "(Kein Thema angegeben — erstelle ein sinnvolles Deck bzw. überarbeite das bestehende Deck.)",
            minutes=minutes,
            generate_list=generate_list,
            chapters=chapters or [],
            course_media=course_media or [],
            course_tasks=course_tasks or [],
            current_title=current_title,
            current_content=current_content,
            markdown_manual=SLIDES_MARKDOWN_MANUAL,
            edits_spec=SLIDES_CONTENT_EDITS_SPEC,
        )

        return await self._call_with_json(
            prompt,
            response_format={"type": "json_object"},
            config=self._public_config(config),
            max_tokens=SLIDES_MAX_TOKENS,
        )

    async def generate_applet(
        self,
        prompt: str,
        existing_html: str = "",
        config: Optional[dict] = None,
    ):
        """Generiert/überarbeitet ein interaktives HTML-Applet via LLM.

        prompt: Was das Applet zeigen/ermöglichen soll (bzw. Änderungswunsch
        bei existing_html). existing_html: aktueller HTML-Code (Refinement).
        Das LLM liefert JSON: {"html", "title", "description"} — wird hier
        NICHT gespeichert (Preview-first im Applet-Studio).

        Applets enthalten keine Studentendaten — nutzt den Public Endpoint,
        falls konfiguriert.
        """
        tpl = Template(APPLET_PROMPT_TEMPLATE).render(
            prompt=prompt,
            existing_html=existing_html,
        )

        # Komplettes HTML-Dokument → großzügiges Timeout + Token-Budget.
        cfg = dict(config or {})
        cfg["timeout"] = max(cfg.get("timeout", self.timeout), APPLET_TIMEOUT)

        return await self._call_with_json(
            tpl,
            response_format={"type": "json_object"},
            config=self._public_config(cfg),
            max_tokens=APPLET_MAX_TOKENS,
        )

    async def generate_socratic_hint(
        self,
        task_description: str,
        model_solution: str,
        code_template: str,
        current_solution: str,
        previous_submissions: str,
        hint_history: str,
        student_question: str,
        script_context: str = "",
        media_context: str = "",
        custom_prompt: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        """Generiert einen sokratischen Hinweis fuer einen Studenten.

        Verwendet die Sokratische Methode: Stellt Fragen, gibt gezielte Hinweise,
        aber verratet niemals die direkte Loesung.

        Returns JSON mit:
            hint: Markdown-formatierter Text
            suggestion_type: 'question' | 'hint' | 'encouragement' | 'correction'
        """
        prompt = self._render_prompt(
            custom_prompt or SOCRATIC_HINT_PROMPT_TEMPLATE,
            task_description=task_description,
            model_solution=model_solution,
            code_template=code_template or "(Kein Code-Template)",
            current_solution=current_solution or "(Noch keine Loesung)",
            previous_submissions=previous_submissions or "(Keine vorherigen Abgaben)",
            hint_history=hint_history or "(Dies ist die erste Frage)",
            student_question=student_question,
            script_context=script_context,
            media_context=media_context,
        )

        return await self._call_with_json(prompt, response_format={"type": "json_object"}, config=config)

    async def generate_code_template_and_tests(
        self,
        description: str,
        model_solution: str,
        config: Optional[dict] = None,
    ):
        """Generiert für eine Code-Aufgabe: Code-Vorlage, Public und Private Tests.

        Benötigt die bereits generierte Musterlösung als Refernz.

        Enthält keine sensitive Studentendaten — nutzt daher den Public
        Endpoint, falls konfiguriert.

        Returns JSON mit:
            code_template, public_tests, private_tests
        """
        prompt = self._render_prompt(
            CODE_TEMPLATE_TESTS_PROMPT_TEMPLATE,
            description=description,
            model_solution=model_solution,
        )

        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=self._public_config(config)
        )

    async def convert_image_to_latex(
        self,
        image_base64: str,
        mime_type: str = "image/png",
        config: Optional[dict] = None,
    ):
        """Konvertiert ein Foto einer handgeschriebenen Notiz mit Formeln in Markdown mit LaTeX.

        Nutzt die multimodalen Faehigkeiten des LLM, um das Bild zu analysieren
        und den enthaltenen Text, Graphen und die enthaltene(n) Formel(n) als Markdown mit LaTeX-Code zurueckzugeben.
        """
        client = self._get_client(config)
        model = config.get("model", self.model) if config else self.model
        timeout = config.get("timeout", self.timeout) if config else self.timeout
        is_temp = config is not None
        url, model, is_public = self._effective_endpoint(config)

        max_retries = 2
        last_error = None
        total_ms = 0
        last_content = ""
        last_thinking = ""

        image_prompt_text = (
            "Konvertiere die Formel(n) in diesem Foto in Markdown, Mermaid und LaTeX-Code. "
            "Gib NUR den Markdown, Mermaid bzw LaTeX-Code zurueck. "
            "Enthält das Bild keinen Text oder Formeln, gib einen leeren String zurück."
        )
        # Bild-Base64 wird NICHT geloggt (Speicher + Datenschutz)
        log_prompt = f"[BILD: {mime_type}, {len(image_base64)} Zeichen Base64] {image_prompt_text}"

        system_prompt = (
            "Du bist ein Experte fuer das Erkennen von Text, Graphen und mathematischen Formeln in Bildern. "
            "Analysiere das Foto und konvertiere den Text und alle sichtbaren Graphen und Formeln in gueltigen Markdown mit Mermaid oder LaTeX-Code. "
            "Antworte NUR mit Markdown, Mermaid und LaTeX-Code, keine Erklaerungen. "
            "Nutze ```mermaid ... ``` für Mermaid und $ ... $ fuer inline Math und $$ ... $$ fuer display Math. "
        )

        deadline = time.monotonic() + timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"convert_image_to_latex total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{image_base64}"
                                        },
                                    },
                                    {
                                        "type": "text",
                                        "text": image_prompt_text,
                                    },
                                ],
                            },
                        ],
                        temperature=0.0,
                    ),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                total_ms += round(elapsed * 1000)
                content = response.choices[0].message.content or "(keine Antwort)"
                last_content = content
                last_thinking = self._extract_thinking(response)

                if is_temp:
                    await client.close()

                record_llm_debug_entry(
                    label="CONVERT_IMAGE_TO_LATEX", model=model, url=url, is_public=is_public,
                    system_prompt=system_prompt, prompt=log_prompt,
                    response=content, thinking=last_thinking,
                    success=True, latency_ms=total_ms, attempts=attempt + 1,
                )
                return {
                    "success": True,
                    "data": {"latex": content.strip()},
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"convert_image_to_latex timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"convert_image_to_latex error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        if is_temp:
            await client.close()

        record_llm_debug_entry(
            label="CONVERT_IMAGE_TO_LATEX", model=model, url=url, is_public=is_public,
            system_prompt=system_prompt, prompt=log_prompt,
            response=last_content, thinking=last_thinking,
            success=False, error=last_error, latency_ms=total_ms, attempts=max_retries,
        )
        return {
            "success": False,
            "error": last_error,
            "data": {"latex": ""},
            "latency_ms": 0,
            "raw_response": "",
        }

    async def describe_media_image(
        self,
        image_base64: str,
        mime_type: str = "image/png",
        config: Optional[dict] = None,
    ):
        """Erstellt per LLM einen Titel und eine kurze Beschreibung für ein Kurs-Medium.

        Nutzt die multimodalen Faehigkeiten des LLM, um das Bild zu analysieren.

        Returns JSON mit:
            title, description
        """
        client = self._get_client(config)
        model = config.get("model", self.model) if config else self.model
        timeout = config.get("timeout", self.timeout) if config else self.timeout
        is_temp = config is not None
        url, model, is_public = self._effective_endpoint(config)

        max_retries = 2
        last_error = None
        total_ms = 0
        last_content = ""
        last_thinking = ""

        image_prompt_text = (
            "Beschreibe dieses Medium fuer eine Kurs-Medienbibliothek. "
            "Der Titel wird in Markdown-Referenzen verwendet, die "
            "Beschreibung hilft, das Medium korrekt in Skript, "
            "Slides oder Aufgaben einzubinden."
        )
        # Bild-Base64 wird NICHT geloggt (Speicher + Datenschutz)
        log_prompt = f"[BILD: {mime_type}, {len(image_base64)} Zeichen Base64] {image_prompt_text}"

        system_prompt = (
            "Du bist ein Experte fuer die Beschreibung von Kurs-Medien (Abbildungen, Diagramme, "
            "Plots, Fotos) in Lehrmaterialien. Analysiere das Bild und erstelle "
            "(1) einen kurzen, aussagekraeftigen Titel (max. 8 Woerter, kein voelliger Satz) und "
            "(2) eine praezise Beschreibung (2-4 Saetze), die erklaert, was das Medium zeigt und "
            "welchen Lehrinhalt es illustriert. "
            'Antworte NUR mit einem JSON-Objekt der Form {"title": "...", "description": "..."} '
            "ohne Code-Bloecke oder Erklaerungen."
        )

        deadline = time.monotonic() + timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning("describe_media_image total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{image_base64}"
                                        },
                                    },
                                    {
                                        "type": "text",
                                        "text": image_prompt_text,
                                    },
                                ],
                            },
                        ],
                        temperature=0.0,
                    ),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                total_ms += round(elapsed * 1000)
                content = response.choices[0].message.content or ""
                last_content = content
                last_thinking = self._extract_thinking(response)

                result = None
                if content:
                    try:
                        result = json.loads(content)
                    except json.JSONDecodeError:
                        result = self._extract_json(content)
                if result is None or not isinstance(result.get("description"), str):
                    last_error = "LLM hat kein gultiges JSON geliefert"
                    logger.warning(f"describe_media_image JSON extraction failed (attempt {attempt+1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1 * (attempt + 1))
                    continue

                if is_temp:
                    await client.close()

                record_llm_debug_entry(
                    label="DESCRIBE_MEDIA_IMAGE", model=model, url=url, is_public=is_public,
                    system_prompt=system_prompt, prompt=log_prompt,
                    response=content, thinking=last_thinking,
                    success=True, latency_ms=total_ms, attempts=attempt + 1,
                )
                return {
                    "success": True,
                    "data": {
                        "title": (result.get("title") or "").strip(),
                        "description": (result.get("description") or "").strip(),
                    },
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"describe_media_image timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"describe_media_image error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        if is_temp:
            await client.close()

        record_llm_debug_entry(
            label="DESCRIBE_MEDIA_IMAGE", model=model, url=url, is_public=is_public,
            system_prompt=system_prompt, prompt=log_prompt,
            response=last_content, thinking=last_thinking,
            success=False, error=last_error, latency_ms=total_ms, attempts=max_retries,
        )
        return {
            "success": False,
            "error": last_error,
            "data": {"title": "", "description": ""},
            "latency_ms": 0,
            "raw_response": "",
        }

    async def generate_course_report(
        self,
        course_name: str,
        tasks_data: str,
        students_data: str,
        config: Optional[dict] = None,
    ):
        """Generiert einen detaillierten Kurs-Performance-Report als Markdown.

        Analysiert die Daten aller gefilterten Aufgaben und Studierenden und erstellt
        einen strukturierten Report fuer Tutoren.

        Args:
            course_name: Name des Kurses
            tasks_data: Formatierter Text mit Aufgaben-Informationen (Titel, Typ, Punkte, etc.)
            students_data: Formatierter Text mit Studentendaten (Einreichungen, Feedback, Hinweise)
            config: Optionale LLM-Konfiguration pro Kurs

        Returns:
            Dict mit "success", "data"{"report": markdown_text}, "latency_ms", "raw_response"
        """
        prompt = self._render_prompt(
            COURSE_REPORT_PROMPT_TEMPLATE,
            course_name=course_name,
            tasks_data=tasks_data,
            students_data=students_data,
        )

        result = await self._call_plain(prompt, config=config)

        # Passe das Return-Format an, um "report" statt "model_solution" zu verwenden
        if result["success"]:
            result["data"] = {"report": result["data"]["model_solution"]}

        return result

    async def generate_student_report(
        self,
        course_name: str,
        tasks_data: str,
        student_data: str,
        config: Optional[dict] = None,
    ):
        """Generiert einen persönlichen Performance-Report für einen Studenten als Markdown.

        Analysiert die Daten der gefilterten Aufgaben des Studenten und erstellt
        einen strukturierten Report mit Tipps und Empfehlungen zur Verbesserung.

        Args:
            course_name: Name des Kurses
            tasks_data: Formatierter Text mit Aufgaben-Informationen (Titel, Typ, Punkte, etc.)
            student_data: Formatierter Text mit den persönlichen Daten des Studenten
                (Einreichungen, Feedback, Hinweise, Bearbeitungsdauer)
            config: Optionale LLM-Konfiguration pro Kurs

        Returns:
            Dict mit "success", "data"{"report": markdown_text}, "latency_ms", "raw_response"
        """
        prompt = self._render_prompt(
            STUDENT_REPORT_PROMPT_TEMPLATE,
            course_name=course_name,
            tasks_data=tasks_data,
            student_data=student_data,
        )

        result = await self._call_plain(prompt, config=config)

        # Passe das Return-Format an, um "report" statt "model_solution" zu verwenden
        if result["success"]:
            result["data"] = {"report": result["data"]["model_solution"]}

        return result

    async def answer_script_question(
        self,
        course_name: str,
        section_context: str,
        quote_context: str,
        chapter_index: str,
        question_history: str,
        student_question: str,
        config: Optional[dict] = None,
    ):
        """Beantwortet eine Studenten-Frage zum Skript (Freitext, kein JSON).

        section_context: Markdown-Inhalt des zitierten Kapitels (ggf. gekürzt)
            oder Hinweis, dass die Frage allgemein ist.
        quote_context: Textauswahl des Studenten oder Hinweis, dass keine da ist.
        chapter_index: Titel (+ Zusammenfassung) der sichtbaren Kapitel —
            für konsistente Querverweise (@fig:/@eq:).
        question_history: Die letzten Fragen des Studenten (Kontext).

        Returns:
            Dict mit "success", "data"{"text": markdown}, "latency_ms", "raw_response"
        """
        prompt = self._render_prompt(
            SCRIPT_QUESTION_PROMPT_TEMPLATE,
            course_name=course_name,
            section_context=section_context,
            quote_context=quote_context,
            chapter_index=chapter_index,
            question_history=question_history,
            student_question=student_question,
        )

        result = await self._call_plain(prompt, config=config)

        # Passe das Return-Format an: "text" statt "model_solution"
        if result["success"]:
            result["data"] = {"text": result["data"]["model_solution"]}

        return result

    # ── Import (Kurs-Materialien aus Zip) ─────────────────────

    async def import_analyze_file(
        self,
        filename: str,
        line_range: str,
        chunk_text: str,
        config: Optional[dict] = None,
    ):
        """Struktur-Digest (JSON) für einen Text-Chunk: summary + headings."""
        prompt = self._render_prompt(
            FILE_SUMMARY_PROMPT_TEMPLATE,
            filename=filename,
            line_range=line_range,
            chunk_text=chunk_text,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_script_planner_step(
        self,
        file_tree: str,
        digests: str,
        main_tex: str,
        course_sections: str,
        steps: str,
        config: Optional[dict] = None,
    ):
        """Ein Schritt des agentic Skript-Kapitel-Planners (JSON-Action)."""
        prompt = self._render_prompt(
            SCRIPT_PLANNER_PROMPT_TEMPLATE,
            file_tree=file_tree,
            digests=digests,
            main_tex=main_tex,
            course_sections=course_sections,
            steps=steps,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_slides_planner_step(
        self,
        file_tree: str,
        digests: str,
        zip_decks: str,
        course_decks: str,
        course_sections: str,
        steps: str,
        config: Optional[dict] = None,
    ):
        """Ein Schritt des agentic Folien-Planners (JSON-Action)."""
        prompt = self._render_prompt(
            SLIDES_PLANNER_PROMPT_TEMPLATE,
            file_tree=file_tree,
            digests=digests,
            zip_decks=zip_decks,
            course_decks=course_decks,
            course_sections=course_sections,
            steps=steps,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_existing_desc_step(
        self,
        items: str,
        file_tree: str,
        digests: str,
        config: Optional[dict] = None,
    ):
        """Beschreibungen für bestehende Kurs-Materialien, die von den
        Import-Materialien betroffen sind (JSON: {"plans": [{"id", "description"}]})."""
        prompt = self._render_prompt(
            EXISTING_DESC_PROMPT_TEMPLATE,
            items=items,
            file_tree=file_tree,
            digests=digests,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_gather_step(
        self,
        kind: str,
        kind_rules: str,
        title: str,
        description: str,
        draft_sources: str,
        file_tree: str,
        digests: str,
        sections: str,
        decks: str,
        media: str,
        references: str,
        steps: str,
        config: Optional[dict] = None,
    ):
        """Ein Schritt der agentic Quellen-Sammlung (JSON-Action)."""
        prompt = self._render_prompt(
            GATHER_PROMPT_TEMPLATE,
            kind=kind,
            kind_rules=kind_rules,
            title=title,
            description=description,
            draft_sources=draft_sources,
            file_tree=file_tree,
            digests=digests,
            sections=sections,
            decks=decks,
            media=media,
            references=references,
            steps=steps,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_ref_extract_step(
        self,
        bib_text: str,
        known_refs: str,
        digests: str,
        steps: str,
        config: Optional[dict] = None,
    ):
        """Ein Schritt der agentic Quellen-Extraktion (JSON-Action)."""
        prompt = self._render_prompt(
            REF_EXTRACT_PROMPT_TEMPLATE,
            bib_text=bib_text,
            known_refs=known_refs,
            digests=digests,
            steps=steps,
        )
        return await self._call_with_json(
            prompt, response_format={"type": "json_object"}, config=config
        )

    async def import_convert_chapter(
        self,
        chapter_title: str,
        chapter_position: str,
        part_info: str,
        other_chapters: str,
        label_map: str,
        macro_map: str,
        image_map: str,
        source_text: str,
        edit_note: str = "",
        references: str = "",
        config: Optional[dict] = None,
    ):
        """Wortgetreue Konvertierung eines Quelltext-Ausschnitts in Skript-Markdown.

        edit_note: leer für normale Konvertierung; im Edit-Modus (bestehendes
        Kapitel wird aus Import-Materialien (neu)generiert) Anweisung zum
        Mergen von Bestand und neuem Inhalt.
        references: Kurs-Quellenverzeichnis (Zitations-Keys) als Kontext.
        """
        prompt = self._render_prompt(
            CONVERT_CHAPTER_PROMPT_TEMPLATE,
            chapter_title=chapter_title,
            chapter_position=chapter_position,
            part_info=part_info,
            edit_note=edit_note,
            other_chapters=other_chapters,
            label_map=label_map,
            macro_map=macro_map,
            image_map=image_map,
            references=references,
            script_format=SCRIPT_FORMAT_SPEC,
            source_text=source_text,
        )
        return await self._call_plain(prompt, config=config)

    async def import_chapter_summary(
        self,
        chapter_title: str,
        chapter_content: str,
        config: Optional[dict] = None,
    ):
        """Kurze interne Zusammenfassung eines generierten Skript-Kapitels."""
        prompt = self._render_prompt(
            CHAPTER_SUMMARY_PROMPT_TEMPLATE,
            chapter_title=chapter_title,
            chapter_content=chapter_content,
        )
        return await self._call_plain(prompt, config=config)

    async def import_describe_reference(
        self,
        entry_text: str,
        config: Optional[dict] = None,
    ):
        """Inhalts-/Kernpunkt-Beschreibung einer importierten Bibliographie-Quelle.

        Ziel: Das LLM im Kurs soll anhand der Beschreibung entscheiden können,
        WANN diese Quelle zitiert werden sollte.
        """
        prompt = (
            "Du pflegst die wissenschaftliche Quellenbibliothek eines Kurses. "
            "Gegeben ist eine Bibliographie-Quelle:\n\n"
            f"{entry_text}\n\n"
            "Erstelle eine kurze Beschreibung (1-3 Saetze) des Inhalts bzw. der Kernpunkte "
            "dieser Quelle, sodass ein LLM anhand der Beschreibung entscheiden kann, "
            "WANN diese Quelle zitiert werden sollte. Nutze dein Wissen ueber bekannte "
            "Werke; ist dir das Werk unbekannt, leite die Beschreibung konservativ aus "
            "Titel und Kontextfeldern ab und merke das in einem kurzen Zusatz an. "
            "Antworte NUR mit dem Beschreibungstext, ohne Anfuehrungszeichen oder Einleitung."
        )
        return await self._call_plain(prompt, config=config)

    async def import_generate_slide_deck(
        self,
        chapter_title: str,
        max_slides: int,
        context: str,
        source_slides: str,
        source: str,
        config: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ):
        """Slide-Deck für ein Kapitel (plain Text im Slide-Format).

        max_tokens: optional höheres Output-Budget (z. B. 1:1-Decks mit vielen Folien).
        """
        prompt = self._render_prompt(
            SLIDE_DECK_PROMPT_TEMPLATE,
            chapter_title=chapter_title,
            max_slides=max_slides,
            context=context,
            source_slides=source_slides,
            slide_format=SLIDE_FORMAT_SPEC,
            source=source,
        )
        return await self._call_plain(prompt, config=config, max_tokens=max_tokens or SLIDES_MAX_TOKENS)

    async def import_refine_chapter(
        self,
        chapter_title: str,
        generated: str,
        source_text: str,
        image_checklist: str = "",
        other_chapters: str = "",
        config: Optional[dict] = None,
    ):
        """Verifikations-Refinement eines generierten Kapitels (Vollständigkeits-Check).

        Das LLM vergleicht den Entwurf mit dem Quelltext und liefert JSON:
        entweder {"content_edits": [...]} (lokal, bevorzugt) oder
        {"content": "..."} (Volltext-Fallback) bzw. {} (nichts zu korrigieren).
        image_checklist: Medien-URLs aus dem Quelltext, die alle im Ergebnis
        vorkommen müssen.
        other_chapters: Summaries der anderen Kapitel (Querverweise/Notation).
        """
        prompt = self._render_prompt(
            REFINE_CHAPTER_PROMPT_TEMPLATE,
            chapter_title=chapter_title,
            other_chapters=other_chapters or "(keine)",
            generated=generated,
            source_text=source_text,
            image_checklist=image_checklist,
            edits_spec=SCRIPT_CONTENT_EDITS_SPEC,
        )
        return await self._call_with_json(prompt, response_format={"type": "json_object"}, config=config)

    async def import_refine_slide_deck(
        self,
        deck_title: str,
        generated: str,
        source: str,
        count_note: str = "",
        count_check: str = "",
        other_context: str = "",
        config: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ):
        """Verifikations-Refinement eines generierten Slide-Decks (Vollständigkeits-Check).

        Das LLM vergleicht das Deck mit den Quellen und liefert JSON:
        entweder {"content_edits": [...]} (lokal, bevorzugt) oder
        {"content": "..."} (Volltext-Fallback) bzw. {} (nichts zu korrigieren).
        generated: Deck INKLUSIVE „%% Folie N %%“-Marker (s. numbered_slide_content)
        — die Marker sind KEIN Deck-Inhalt und werden nicht zurückgeliefert.
        other_context: Summaries der anderen Decks + Skript-Kapitel
        (Querverweise/Notation).
        count_note/count_check: Anweisungen zur erwarteten Foliengenzahl
        (1:1-Modus: exakt so viele wie Quell-Folien).
        """
        prompt = self._render_prompt(
            REFINE_SLIDE_DECK_PROMPT_TEMPLATE,
            deck_title=deck_title,
            generated=generated,
            source=source,
            count_note=count_note,
            count_check=count_check,
            other_context=other_context or "(keine)",
            edits_spec=SLIDES_CONTENT_EDITS_SPEC,
        )
        return await self._call_with_json(prompt, response_format={"type": "json_object"}, config=config, max_tokens=max_tokens or SLIDES_MAX_TOKENS)

    async def import_deck_summary(
        self,
        deck_title: str,
        deck_content: str,
        config: Optional[dict] = None,
    ):
        """Kurze interne Zusammenfassung eines generierten Slide-Decks."""
        prompt = self._render_prompt(
            DECK_SUMMARY_PROMPT_TEMPLATE,
            deck_title=deck_title,
            deck_content=deck_content,
        )
        return await self._call_plain(prompt, config=config)

    # ── Private call methods ───────────────────────────────────

    async def _call_plain(self, prompt: str, config: Optional[dict] = None, max_tokens: Optional[int] = None):
        """Einfacher LLM-Aufruf ohne JSON-Parser — gibt rohen Text zurück.

        Falls config uebergeben wird, wird ein temporares Client mit dieser Config
        verwendet (unterstuetzt global_settings / course_settings Resolver).
        max_tokens: optionale Obergrenze fuer die Antwortlaenge (sonst Modell-Default).
        """
        max_retries = 2
        last_error = None
        client = self._get_client(config)
        model = config.get("model", self.model) if config else self.model
        timeout = config.get("timeout", self.timeout) if config else self.timeout
        url, model, is_public = self._effective_endpoint(config)
        label = _debug_caller_label()
        system_prompt = "Du bist ein hilfsbereiter Tutor."
        total_ms = 0
        last_content = ""
        last_thinking = ""

        deadline = time.monotonic() + timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"_call_plain total timeout")
                break

            try:
                start = time.time()

                create_kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": self.temperature,
                }
                if max_tokens is not None:
                    create_kwargs["max_tokens"] = max_tokens

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                total_ms += round(elapsed * 1000)
                content = response.choices[0].message.content or "(keine Antwort)"
                last_content = content
                last_thinking = self._extract_thinking(response)

                if config:
                    await client.close()

                record_llm_debug_entry(
                    label=label, model=model, url=url, is_public=is_public,
                    system_prompt=system_prompt, prompt=prompt,
                    response=content, thinking=last_thinking,
                    success=True, latency_ms=total_ms, attempts=attempt + 1,
                )
                return {
                    "success": True,
                    "data": {"model_solution": content.strip()},
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"_call_plain timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"_call_plain error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        if config:
            await client.close()

        record_llm_debug_entry(
            label=label, model=model, url=url, is_public=is_public,
            system_prompt=system_prompt, prompt=prompt,
            response=last_content, thinking=last_thinking,
            success=False, error=last_error, latency_ms=total_ms, attempts=max_retries,
        )
        return {
            "success": False,
            "error": last_error,
            "data": {"model_solution": ""},
            "latency_ms": 0,
            "raw_response": "",
        }

    async def _call_with_json(self, prompt: str, response_format: Optional[dict] = None, config: Optional[dict] = None, max_tokens: Optional[int] = None):
        """
        Generischer LLM-Aufruf mit JSON-Response-Format.

        Falls config uebergeben wird, wird ein temporares Client mit dieser Config
        verwendet (unterstuetzt global_settings / course_settings Resolver).
        max_tokens: optionale Obergrenze für die Antwortlänge (z. B. für große
        HTML-Generierungen), sonst Modell-Default.

        retry=2 bei Fehlern (Rate Limits, Timeouts).
        """
        max_retries = 2
        last_error = None
        client = self._get_client(config)
        model = config.get("model", self.model) if config else self.model
        timeout = config.get("timeout", self.timeout) if config else self.timeout
        url, model, is_public = self._effective_endpoint(config)
        label = _debug_caller_label()
        system_prompt = "Du bist ein hilfsbereiter Tutor. Antworte NUR mit gueltigem JSON."
        total_ms = 0
        last_content = ""
        last_thinking = ""

        create_kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format
        if max_tokens is not None:
            create_kwargs["max_tokens"] = max_tokens

        deadline = time.monotonic() + timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"_call_with_json total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    client.chat.completions.create(**create_kwargs),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                total_ms += round(elapsed * 1000)

                content = response.choices[0].message.content
                last_content = content or ""
                last_thinking = self._extract_thinking(response)
                if content is None:
                    # Antwort wurde abgeschnitten (max_tokens erreicht)
                    last_error = "LLM-Antwort wurde abgeschnitten (max_tokens)."
                    continue

                try:
                    result = json.loads(content)#.replace('\\','\\\\'))
                except json.JSONDecodeError:
                    # Fallback: JSON aus freiem Text extrahieren
                    result = self._extract_json(content)
                    if result is None:
                        last_error = "LLM hat kein gueltiges JSON geliefert"
                        logger.warning(f"_call_with_json JSON extraction failed (attempt {attempt+1}/{max_retries})")
                        continue

                if config:
                    await client.close()

                record_llm_debug_entry(
                    label=label, model=model, url=url, is_public=is_public,
                    system_prompt=system_prompt, prompt=prompt,
                    response=content, thinking=last_thinking,
                    success=True, latency_ms=total_ms, attempts=attempt + 1,
                )
                return {
                    "success": True,
                    "data": result,
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {timeout}s erhalten"
                logger.warning(f"_call_with_json timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"_call_with_json error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        if config:
            await client.close()

        record_llm_debug_entry(
            label=label, model=model, url=url, is_public=is_public,
            system_prompt=system_prompt, prompt=prompt,
            response=last_content, thinking=last_thinking,
            success=False, error=last_error, latency_ms=total_ms, attempts=max_retries,
        )
        return {
            "success": False,
            "error": last_error,
            "data": {},
            "latency_ms": 0,
            "raw_response": "",
        }

    def _public_config(self, config: Optional[dict]) -> Optional[dict]:
        """Config für nicht-sensitive Aufgaben (z. B. Task-/Musterlösung-Generierung).

        Nutzt den Public Endpoint (api_url_public), falls konfiguriert.
        API-Key und Modell fallen pro Feld auf die Private-Endpoint-Config
        zurück. Ohne api_url_public wird die Config unverändert zurückgegeben
        (gleicher Endpoint wie für sensitive Daten).
        """
        if not config:
            return config
        if not config.get("api_url_public"):
            return config
        public = dict(config)
        public["api_url"] = config["api_url_public"]
        public["api_key"] = config.get("api_key_public") or config.get("api_key") or self.api_key
        public["model"] = config.get("model_public") or config.get("model") or self.model
        return public

    def _get_client(self, config: Optional[dict] = None) -> AsyncOpenAI:
        """Gibt einen OpenAI-Client zurueck.

        Falls config uebergeben wird, wird ein temporares Client mit dieser Config
        erstellt. Sonst wird der persistente self.client verwendet.
        """
        if config:
            return AsyncOpenAI(
                base_url=config.get("api_url", self.api_url),
                api_key=config.get("api_key", self.api_key),
                timeout=config.get("timeout", self.timeout),
            )
        return self.client

    def _effective_endpoint(self, config: Optional[dict]) -> tuple[str, str, bool]:
        """Liefert (url, model, is_public) des effektiven Endpoints für einen Call.

        is_public ist True, wenn die effektive URL der konfigurierten
        api_url_public entspricht (z. B. nach _public_config()). Bei identischen
        URLs bleibt es privat (unklar).
        """
        url = (config.get("api_url") if config else None) or self.api_url
        model = (config.get("model") if config else None) or self.model
        is_public = False
        if config:
            public_url = config.get("api_url_public")
            if public_url:
                is_public = public_url.rstrip("/") == url.rstrip("/")
        return url, model, is_public

    @staticmethod
    def _extract_thinking(response) -> str:
        """Extrahiert optionales Reasoning/Thinking aus einer Chat-Completion-Antwort.

        Je nach Provider (z. B. Qwen3 via vLLM/Ollama) liegt das Reasoning in
        reasoning_content/reasoning — teils nur in model_extra.
        """
        try:
            message = response.choices[0].message
            for attr in ("reasoning_content", "reasoning"):
                value = getattr(message, attr, None)
                if isinstance(value, str) and value:
                    return value
            extra = getattr(message, "model_extra", None)
            if isinstance(extra, dict):
                for key in ("reasoning_content", "reasoning"):
                    value = extra.get(key)
                    if isinstance(value, str) and value:
                        return value
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Versucht, JSON aus freiem Text zu extrahieren.

        Prueft zuerst auf ```json-Codeblocks, dann auf { }-Muster.
        Gibt None zurueck, wenn kein gultiges JSON gefunden wurde.
        """
        # First try to extract from ```json ... ``` code blocks
        json_block = None
        cb_start = text.find("```json")
        if cb_start >= 0:
            cb_start = text.find("{", cb_start)
            cb_end = text.rfind("}")
            if cb_start >= 0 and cb_end > cb_start:
                json_block = text[cb_start:cb_end + 1]

        # Also try raw { } extraction
        raw_start = text.find("{")
        raw_end = text.rfind("}") + 1
        raw_block = None
        if raw_start >= 0 and raw_end > raw_start:
            raw_block = text[raw_start:raw_end]

        # Try code block first, then raw
        for block in (json_block, raw_block):
            if block:
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue
        return None
