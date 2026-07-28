"""
Prompt-Templates für LLM-gestützte Musterlösung-Generierung.

Tutor gibt Aufgabenstellung ein → LLM generiert Musterlösung.
"""

SOLUTION_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor. Schreibe eine knappe, präzise Musterlösung.

AUFgabENTITEL: {{ title }}
AUFGABENTYP: {{ task_type_description }}

AUFGABENSTELLUNG:
{{ description }}

{% if code_template_section %}{{ code_template_section }}{% endif %}

Gib NUR die Lösung selbst aus — keine Einleitung, kein JSON, keine Meta-Kommentare.
Bei Code: Nur den funktionalen Code.
Bei Text: Die direkte Antwort/Erläuterung.

Verwende Markdown-Formatierung für bessere Lesbarkeit:
- **fett** für wichtige Begriffe und Kernaussagen
- *kursiv* für Betonungen
- - Listen für Aufzählungen
- $...$ für Inline-Mathematik und $$...$$ für Block-Mathematik (LaTeX)
- ```code``` für kurze Code-Schnipsel

Bitte gib auch Bewertungskriterien an um eine faire Bewertung zu ermöglichen. Es können maximal {{max_points}} Punkte erzielt werden.
"""