"""
Prompt-Templates für LLM-gestützte Aufgabenerstellung.

Tutor gibt Thema + Schwierigkeit ein → LLM generiert einen knappen
Aufgabentitel und eine Aufgabenstellung. Die Musterlösung wird später
in einem separaten LLM-Call („LLM-Musterlösung generieren“) erstellt.
"""

CREATION_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor. Erfinde eine Übungsaufgabe.

THEMA: {{ topic }}
SCHWIERIGKEIT: {{ difficulty }}
AUFGABENTYP: {{ task_type }}
{% if title %}TITEL: {{ title }}{% endif %}

Gib als Antwort ein gültiges JSON-Objekt mit EXAKT diesen beiden Feldern:
{
  "title": "<kurzer, prägnanter Titel>",
  "description": "<vollständige Aufgabenstellung für Studierende>"
}
Achte dabei auf korrektes Escaping von special Characters. In Latex-Umgebungen muss insbesondere der Backslash escaped werden (z.B. $\\text{...}$ oder $$A \\rightarrow B$$). Dollar-Zeichen außerhalb von Code-Blöcken, die kein Latex triggern sollen können mit Backslash \\$ escaped werden.

Regeln:
- Der Titel soll klar sein (z.B. „Blatt3-01: Rekursion“)
- Die Aufgabenstellung soll präzise formuliert sein
- Die Schwierigkeit muss der Vorgabe entsprechen
- Bei Text-Aufgaben: Beschreibe die Aufgabenstellung. Verwende Markdown-Formatierung (**fett**, *kursiv*, Listen, $Math$, $$Display-Math$$) für bessere Lesbarkeit.
- Bei Code-Aufgaben: Beschreibe was implementiert werden soll. Verwende $...$ für mathematische Notation.
- Wenn Graphen zur Beschreibung benätigt werden: Verwende Mermaid (```mermaid ... ```) in Markdown.
- Keine Musterlösung, keine Punkte — das kommt später
"""
