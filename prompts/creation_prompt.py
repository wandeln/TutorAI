"""
Prompt-Templates für LLM-gestützte Aufgabenerstellung.

Tutor gibt Thema + Schwierigkeit ein → LLM generiert Vorschlag.
"""

CREATION_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor und erstellst Übungsaufgaben für Studierende.

THEMA: {topic}
SCHWIERIGKEIT: {difficulty}
AUFGABENTYP: {task_type}

{% if context %}
ZUSÄTZLICHER KONTEXT: {context}
{% endif %}

Erstelle eine gut durchdachte Aufgabe und gib zurück (JSON):
{{
  "title": "<kurzer Titel>",
  "description": "<detaillierte Aufgabenstellung, klar formuliert>",
  "model_solution": "<korrekte Musterlösung>",
  "max_points": <faire Punktzahl, 5-15>,
  "hints": ["<2-3 Hinweise für Studierende, die Hilfe benötigen>"],
  "learning_objectives": ["<was der Student durch diese Aufgabe lernen soll>"],
  "common_mistakes": ["<typische Fehler die Studierende machen>"],
  {% if task_type == "code" %}
  "code_template": "<Code-Gerüst mit TODO-Kommentaren>",
  "public_tests": ["<2-3 einfache Unit-Tests als Python-Code>"],
  {% endif %}
  {% if task_type == "mc" %}
  "options": ["<Option A>", "<Option B>", "<Option C>", "<Option D>"],
  "correct_option_index": <0-basierter Index der richtigen Antwort>,
  "explanations": ["<Erklärung warum jede Option richtig/falsch ist>"],
  {% endif %}
}}

Anforderungen:
- Die Aufgabe sollte den Lernzielen entsprechen
- Schwierigkeit muss der Vorgabe gerecht werden
- Bei Code-Aufgaben: Template mit klaren TODO-Markern
- Bei MC-Aufgaben: 4 Optionen, davon nur 1 richtig, plausible Ablenkungen
- Musterlösung sollte verständlich und nachvollziehbar sein
"""