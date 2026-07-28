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

Gib als Antwort ein JSON-Objekt mit EXAKT diesen beiden Feldern:
{
  "title": "<kurzer, prägnanter Titel>",
  "description": "<vollständige Aufgabenstellung für Studierende>"
}

Regeln:
- Der Titel soll klar sein (z.B. „Blatt3-01: Rekursion“)
- Die Aufgabenstellung soll präzise formuliert sein
- Die Schwierigkeit muss der Vorgabe entsprechen
- Bei Text-Aufgaben: Beschreibe die Aufgabenstellung
- Bei Code-Aufgaben: Beschreibe was implementiert werden soll
- Bei MC-Aufgaben: Formuliere die Frage und liste die Optionen
- Keine Musterlösung, keine Punkte — das kommt später
"""
