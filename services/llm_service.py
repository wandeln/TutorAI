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
import time
from typing import Optional, Dict, Any, List
from datetime import datetime

from openai import AsyncOpenAI

from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT
from prompts.grading_prompt import GRADING_TEXT_PROMPT_TEMPLATE, GRADING_CODE_PROMPT_TEMPLATE
from prompts.creation_prompt import CREATION_PROMPT_TEMPLATE
from prompts.solution_prompt import SOLUTION_PROMPT_TEMPLATE


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
    
    async def grade_text_task(
        self,
        task_description: str,
        model_solution: str,
        student_solution: str,
        max_points: int,
        custom_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Korrigiert eine Textaufgabe via LLM."""
        
        prompt = (custom_prompt or GRADING_TEXT_PROMPT_TEMPLATE).format(
            task_description=task_description,
            model_solution=model_solution,
            student_solution=student_solution,
            max_points=max_points,
        )
        
        return await self._call_with_json(prompt)
    
    async def grade_code_task(
        self,
        task_description: str,
        model_solution: str,
        student_code: str,
        test_results: str,
        max_points: int,
        custom_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Korrigiert eine Codeaufgabe via LLM (inkl. Test-Ergebnissen)."""
        
        prompt = (custom_prompt or GRADING_PROMPT_TEMPLATE).format(
            task_description=task_description,
            model_solution=model_solution,
            student_solution=student_code,
            test_results=test_results,
            max_points=max_points,
        )
        
        return await self._call_with_json(prompt)
    
    async def suggest_task(
        self,
        topic: str,
        difficulty: str,
        task_type: str,
        context: str = "",
    ) -> Dict[str, Any]:
        """Generiert Aufgabenvorschlag für Tutoren."""
        
        prompt = CREATION_PROMPT_TEMPLATE.format(
            topic=topic,
            difficulty=difficulty,
            task_type=task_type,
            context=context,
        )
        
        return await self._call_with_json(prompt)

    async def generate_model_solution(
        self,
        description: str,
        task_type: str,
        max_points: int,
        code_template: str = "",
    ) -> Dict[str, Any]:
        """Generiert Musterlösung und Code-Template für eine gegebene Aufgabenstellung."""
        
        code_template_section = ""
        if task_type == "code" and code_template:
            code_template_section = f"CODE-TEMPLATE:\n{code_template}\n\n"
        
        prompt = SOLUTION_PROMPT_TEMPLATE.format(
            description=description,
            task_type=task_type,
            max_points=max_points,
            code_template_section=code_template_section,
        )
        
        return await self._call_with_json(prompt)
    
    async def _call_with_json(self, prompt: str) -> Dict[str, Any]:
        """
        Generischer LLM-Aufruf mit JSON-Response-Format.
        
        retry=2 bei Fehlern (Rate Limits, Timeouts).
        """
        max_retries = 2
        last_error = None
        
        for attempt in range(max_retries):
            try:
                start = time.time()
                
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Du bist ein hilfsbereiter Tutor. Antworte NUR mit gültigem JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self.temperature,
                )
                
                elapsed = time.time() - start
                
                content = response.choices[0].message.content
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
                
            except Exception as e:
                last_error = str(e)
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