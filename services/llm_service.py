"""
LLM-Service: Kommunikation mit OpenAI-kompatiblen Endpoints.

Unterstützt:
- Qwen3, Llama, Mistral, etc. (jedes OpenAI-kompatible Modell)
- Grading (Korrektur) mit JSON-Response
- Task-Suggestions (Aufgabenvorschläge)
- Config pro Kurs (URL, Modell, Prompt)
"""

import asyncio
import json
import logging
import time
from typing import Optional, Any

from jinja2 import Template
from openai import AsyncOpenAI

from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT
from prompts.grading_prompt import GRADING_TEXT_PROMPT_TEMPLATE, GRADING_CODE_PROMPT_TEMPLATE
from prompts.creation_prompt import CREATION_PROMPT_TEMPLATE
from prompts.solution_prompt import SOLUTION_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


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
    ):
        """Korrigiert eine Textaufgabe via LLM."""

        prompt = self._render_prompt(
            custom_prompt or GRADING_TEXT_PROMPT_TEMPLATE,
            task_description=task_description,
            model_solution=model_solution,
            student_solution=student_solution,
            max_points=max_points,
        )

        return await self._call_with_json(prompt, response_format={"type": "json_object"})

    async def grade_code_task(
        self,
        task_description: str,
        model_solution: str,
        student_code: str,
        test_results: str,
        max_points: int,
        custom_prompt: Optional[str] = None,
    ):
        """Korrigiert eine Codeaufgabe via LLM (inkl. Test-Ergebnissen)."""

        prompt = self._render_prompt(
            custom_prompt or GRADING_CODE_PROMPT_TEMPLATE,
            task_description=task_description,
            model_solution=model_solution,
            student_solution=student_code,
            test_results=test_results,
            max_points=max_points,
        )

        return await self._call_with_json(prompt, response_format={"type": "json_object"})

    async def suggest_task(
        self,
        topic: str,
        difficulty: str,
        task_type: str,
        title: str = "",
        context: str = "",
    ):
        """Generiert knappen Aufgabenvorschlag (nur Titel + Aufgabenstellung)."""

        prompt = Template(CREATION_PROMPT_TEMPLATE).render(
            topic=topic,
            difficulty=difficulty,
            task_type=task_type,
            title=title,
            context=context,
        )

        return await self._call_with_json(prompt)

    async def generate_model_solution(
        self,
        description: str,
        task_type: str,
        max_points: int,
        code_template: str = "",
        title: str = "",
    ):
        """Generiert Musterlösung als einfachen Text (kein JSON)."""

        code_template_section = ""
        if task_type == "code" and code_template:
            code_template_section = f"CODE-TEMPLATE:\n{code_template}\n\n"

        task_type_description = {"text": "Textaufgabe", "code": "Codeaufgabe"}.get(task_type, task_type)

        prompt = Template(SOLUTION_PROMPT_TEMPLATE).render(
            title=title,
            task_type_description=task_type_description,
            description=description,
            task_type=task_type,
            code_template_section=code_template_section,
            max_points=max_points,
        )

        return await self._call_plain(prompt)

    async def convert_image_to_latex(
        self,
        image_base64: str,
        mime_type: str = "image/png",
    ):
        """Konvertiert ein Foto einer handgeschriebenen Notiz mit Formeln in Markdown mit LaTeX.

        Nutzt die multimodalen Faehigkeiten des LLM, um das Bild zu analysieren
        und den enthaltenen Text und die enthaltene(n) Formel(n) als Markdown mit LaTeX-Code zurueckzugeben.
        """
        max_retries = 2
        last_error = None

        system_prompt = (
            "Du bist ein Experte fuer das Erkennen von Text und mathematischen Formeln in Bildern. "
            "Analysiere das Foto und konvertiere den Text und alle sichtbaren Formeln in gueltigen Markdown mit LaTeX-Code. "
            "Antworte NUR mit Markdown und LaTeX-Code, keine Erklaerungen. "
            "Nutze $ ... $ fuer inline Math und $$ ... $$ fuer display Math. "
        )

        deadline = time.monotonic() + self.timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"convert_image_to_latex total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
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
                                        "text": (
                                            "Konvertiere die Formel(n) in diesem Foto in LaTeX-Code. "
                                            "Gib NUR den LaTeX-Code zurueck."
                                        ),
                                    },
                                ],
                            },
                        ],
                        temperature=0.0,
                    ),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                content = response.choices[0].message.content or "(keine Antwort)"

                return {
                    "success": True,
                    "data": {"latex": content.strip()},
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"convert_image_to_latex timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"convert_image_to_latex error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        return {
            "success": False,
            "error": last_error,
            "data": {"latex": ""},
            "latency_ms": 0,
            "raw_response": "",
        }

    # ── Private call methods ─────────────────────────────────────

    async def _call_plain(self, prompt: str):
        """Einfacher LLM-Aufruf ohne JSON-Parser — gibt rohen Text zurück."""
        max_retries = 2
        last_error = None

        deadline = time.monotonic() + self.timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"_call_plain total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": "Du bist ein hilfsbereiter Tutor."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=self.temperature,
                    ),
                    timeout=remaining,
                )

                elapsed = time.time() - start
                content = response.choices[0].message.content or "(keine Antwort)"

                return {
                    "success": True,
                    "data": {"model_solution": content.strip()},
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"_call_plain timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"_call_plain error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        return {
            "success": False,
            "error": last_error,
            "data": {"model_solution": ""},
            "latency_ms": 0,
            "raw_response": "",
        }

    async def _call_with_json(self, prompt: str, response_format: Optional[dict] = None):
        """
        Generischer LLM-Aufruf mit JSON-Response-Format.

        retry=2 bei Fehlern (Rate Limits, Timeouts).
        """
        max_retries = 2
        last_error = None

        create_kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Du bist ein hilfsbereiter Tutor. Antworte NUR mit gültigem JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format

        deadline = time.monotonic() + self.timeout

        for attempt in range(max_retries):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"_call_with_json total timeout")
                break

            try:
                start = time.time()

                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**create_kwargs),
                    timeout=remaining,
                )

                elapsed = time.time() - start

                content = response.choices[0].message.content
                if content is None:
                    # Antwort wurde abgeschnitten (max_tokens erreicht)
                    last_error = "LLM-Antwort wurde abgeschnitten (max_tokens)."
                    continue

                try:
                    result = json.loads(content)
                except json.JSONDecodeError:
                    # Fallback: JSON aus freiem Text extrahieren
                    result = self._extract_json(content)

                return {
                    "success": True,
                    "data": result,
                    "latency_ms": round(elapsed * 1000),
                    "raw_response": content,
                }

            except asyncio.TimeoutError:
                last_error = f"LLM-Timeout: Antwort nicht innerhalb von {self.timeout}s erhalten"
                logger.warning(f"_call_with_json timeout (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

            except Exception as e:
                last_error = str(e)
                logger.error(f"_call_with_json error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))

        return {
            "success": False,
            "error": last_error,
            "data": {},
            "latency_ms": 0,
            "raw_response": "",
        }

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Versucht, JSON aus freiem Text zu extrahieren (zwischen { })."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return {"error": "Konnte kein JSON extrahieren", "raw": text}
