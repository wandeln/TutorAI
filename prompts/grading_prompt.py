"""
Default-Prompts für LLM-Grading.

Kann vom Admin pro Kurs überschrieben werden (course_settings.grading_prompt).
"""

GRADING_TEXT_PROMPT_TEMPLATE = """\
Du bist ein Tutor, der eine Aufgabenlösung bewertet.

AUFGABE:
{task_description}

MUSTERLÖSUNG:
{model_solution}

STUDENTENLÖSUNG:
{student_solution}

MAXIMALE PUNKTE: {max_points}

Bewerte die Lösung fair und konstruktiv. Gib als Antwort EINZIG ein JSON-Objekt zurück (NICHT in Code-Blöcken, NICHT als Liste).

WICHTIG: Deine gesamte Antwort MUSS ein gültiges JSON-Objekt in geschweiften Klammern {} sein. Verwende KEINE Backticks und KEINE Code-Blöcke.

Beispiel-Antwortformat:
{{
  "points": 7,
  "feedback": "Gute Lösung, aber...",
  "is_correct": false,
  "strengths": ["Klar strukturiert"],
  "improvements": ["Mehr Details"],
  "hints": []
}}

Bewertungskriterien:
- Vollständigkeit: Wurde alle Aspekte der Aufgabe adressiert?
- Korrektheit: Sind die Ergebnisse/Argumente richtig?
- Qualität: Ist die Lösung elegant und gut strukturiert?
- Verständnis: Zeigt der Student echtes Verständnis oder nur Auswendiglernen?
"""


GRADING_CODE_PROMPT_TEMPLATE = """\
Du bist ein Tutor, der eine Code-Lösung bewertet.

AUFGABE:
{task_description}

MUSTERLÖSUNG:
{model_solution}

STUDENTEN-CODE:
{student_solution}

UNIT-TEST-ERGEBNISSE:
{test_results}

MAXIMALE PUNKTE: {max_points}

Bewerte den Code und gib als Antwort EINZIG ein JSON-Objekt zurück (NICHT in Code-Blöcken, NICHT als Liste).

WICHTIG: Deine gesamte Antwort MUSS ein gültiges JSON-Objekt in geschweiften Klammern {} sein. Verwende KEINE Backticks und KEINE Code-Blöcke.

Beispiel-Antwortformat:
{{
  "points": 12,
  "test_points": 10,
  "code_quality_points": 2,
  "feedback": "Guter Code, aber...",
  "is_correct": false,
  "strengths": ["Klar strukturiert"],
  "improvements": ["Mehr Kommentare"],
  "hints": []
}}

Berücksichtige bei der Bewertung:
- Anzahl bestander Tests (public + private)
- Code-Qualität (Lesbarkeit, Struktur, PEP8)
- Effizienz (Laufzeitkomplexität)
- Edge-Cases (Umgang mit Sonderfällen)
- Verständlichkeit (Kommentare, Variablennamen)
"""


# Kompatibilitäts-Alias (verwende GRADING_TEXT_PROMPT_TEMPLATE als Default)
GRADING_PROMPT_TEMPLATE = GRADING_TEXT_PROMPT_TEMPLATE
