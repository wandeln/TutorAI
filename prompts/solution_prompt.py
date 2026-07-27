"""
Prompt-Templates für LLM-gestützte Musterlösung-Generierung.

Tutor gibt Aufgabenstellung ein → LLM generiert Musterlösung.
"""

SOLUTION_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor. Erstelle eine Musterlösung für die folgende Aufgabe.

AUFGABENTYP: {task_type}
AUFGABENSTELLUNG:
{description}

{code_template_section}

Maximale Punkte: {max_points}

Erstelle eine korrekte, verständliche Musterlösung und gib zurück (JSON):
{{
  "model_solution": "<die korrekte Lösung, klar strukturiert>",
  {% if task_type == "code" %}
  "code_template": "<Code-Gerüst mit TODO-Kommentaren für Studenten>",
  {% endif %}
  "explanation": "<kurze Erklärung, warum diese Lösung korrekt ist>",
  "grading_criteria": [
    "<Kriterium 1: was für wie viele Punkte gewertet wird>",
    "<Kriterium 2: was für wie viele Punkte gewertet wird>"
  ]
}}

Anforderungen:
- Die Lösung muss korrekt und vollständig sein
- Bei Code-Aufgaben: gut lesbarer, kommentierter Code
- Bei Textaufgaben: klare, präzise Formulierungen
- grading_criteria sollte detailliert beschreiben, wie die Punkte verteilt werden
"""