"""
Prompt-Template für die einheitliche LLM-gestützte Aufgabengenerierung.

Tutor gibt Thema + Schwierigkeit + zu generierende Felder (Tick-Boxen) ein →
LLM generiert die angeforderten Felder (Titel, Aufgabenstellung, Musterlösung)
neu bzw. ändert vorhandene Inhalte.
Ersetzt CREATION_PROMPT_TEMPLATE, MODIFY_TASK_PROMPT_TEMPLATE und
SOLUTION_PROMPT_TEMPLATE.
"""

UNIFIED_TASK_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor. Du sollst eine Übungsaufgabe erstellen bzw.
bestehende Felder einer Aufgabe ändern/verbessern.

AUFGABENTYP: {{ task_type_description }}
THEMA: {{ topic }}
SCHWIERIGKEIT: {{ difficulty }}

ANZUFORDERNDE FELDER — gib als Antwort ein gültiges JSON-Objekt mit EXAKT diesen Schlüsseln:
{{ generate_list }}

Mögliche Schlüssel und deren Bedeutung:
- "title": Kurzer, prägnanter Titel (z.B. „Blatt3-01: Rekursion")
- "description": Vollständige Aufgabenstellung für Studierende
- "model_solution": Vollständige Musterlösung inkl. Bewertungskriterien

Keine weiteren Schlüssel, keine zusätzlichen Texte, keine Code-Blöcke (```json ... ```).
Achte dabei auf korrektes Escaping von special Characters. In Latex-Umgebungen muss insbesondere der Backslash escaped werden (z.B. $\\text{...}$ oder $$A \\rightarrow B$$). Dollar-Zeichen außerhalb von Code-Blöcken, die kein Latex triggern sollen können mit Backslash \\$ escaped werden.

{% if script_chapters %}

SKRIPT-KAPITEL DES KURSES (mit ihren internen Zusammenfassungen):
{% for ch in script_chapters %}
- {{ ch.title }}{% if ch.summary %} — {{ ch.summary }}{% endif %}
{% endfor %}
Halte die Notation, Schreibweisen und Begriffswahl konsistent mit dem Skript (z.B. gleiche Symbole für gleiche Größen), wo dies sinnvoll ist.
{% endif %}
{% if course_media %}

MEDIEN DES KURSES (Titel — Beschreibung | Einbindung-Snippet):
{% for m in course_media %}
- {{ m.title }}{% if m.description %} — {{ m.description }}{% endif %} | ![{{ m.title }}]({{ m.url }})
{% endfor %}
{% endif %}
{% if current_title %}
BESTEHENDER TITEL:
{{ current_title }}
{% endif %}
{% if current_description %}
BESTEHENDE AUFGABENSTELLUNG:
{{ current_description }}
{% endif %}
{% if current_model_solution %}
BESTEHENDE MUSTERLÖSUNG:
{{ current_model_solution }}
{% endif %}
{% if code_template %}
EXISTIERENDE CODE-VORLAGE (nur als Kontext, NICHT ändern):
{{ code_template }}
{% endif %}

Regeln:
- Generiere NUR die oben angeforderten Felder. Nicht angeforderte Schlüssel dürfen in der Antwort NICHT vorkommen.
- Falls für ein angefordertes Feld bereits ein Inhalt existiert (s. o.), überarbeite/verbessere ihn — halte am Thema fest und gestalte die Aufgabe nicht grundlos neu.
- Falls kein Inhalt existiert, erstelle das Feld neu passend zum Thema.
- Die Felder müssen zueinander passen: Die Musterlösung muss die (ggf. neu formulierte) Aufgabenstellung vollständig lösen.
- Die Aufgabenstellung muss präzise formuliert sein und der angegebenen Schwierigkeit entsprechen.
- Bei Text-Aufgaben: Verwende Markdown-Formatierung (**fett**, *kursiv*, Listen, $Math$, $$Display-Math$$) für bessere Lesbarkeit.
- Bei Code-Aufgaben: Beschreibe in der Aufgabenstellung was implementiert werden soll. Verwende $...$ für mathematische Notation.
- Wenn Graphen zur Beschreibung benötigt werden: Verwende Mermaid (```mermaid ... ```) in Markdown.
- Medien: Du DARFST Medien aus der obigen Medien-Liste in die Aufgabenstellung einbinden, wenn sie inhaltlich wirklich passen (max. 1-2) — verwende dafür exakt den angegebenen /media/-Pfad. Erfinde KEINE anderen Medien-Pfade.
- Querverweise: Bezug auf Abbildungen/Gleichungen aus dem Skript per @fig:label bzw. @eq:label — verwende NUR Labels, die in den obigen Kapitel-Zusammenfassungen vorkommen (sonst ist die Referenz kaputt). Lege in der Aufgabe selbst KEINE neuen fig/eq-Labels an (Kollisionsgefahr).
  WICHTIG: @fig:label / @eq:label sind KEIN Code — schreibe sie IMMER als normalen Fließtext, NIEMALS in Backticks (`...`), Code-Blöcke (``` ... ```) oder Anführungszeichen. Nur so werden sie zu klickbaren Referenzen („Abb. N“ / „Gl. N“) aufgelöst.
  Richtig: „wie in @eq:shannon gezeigt“ — Falsch: „wie in `@eq:shannon` gezeigt“
- Musterlösung: knapp und präzise. Bei Code: Nur den funktionalen Code (passend zur Code-Vorlage — gleiche Signaturen/Struktur). Bei Text: Die direkte Antwort/Erläuterung. Falls es mehrere korrekte Lösungen geben kann, gehe kurz darauf ein.
- Bitte gib in der Musterlösung auch Bewertungskriterien an um eine faire Bewertung zu ermöglichen. Es können maximal {{ max_points }} Punkte erzielt werden.
- Die Bewertungskriterien sollten (abgesehen von standard good practice) keine Punkte enthalten, die aus der Aufgabenstellung nicht ersichtlich sind.
"""
