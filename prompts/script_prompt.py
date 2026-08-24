"""
Prompt-Template für die LLM-gestützte Generierung/Änderung von
Skript-Kapiteln (Vorlesungsskript aus mehreren Markdown-Dateien).

Das LLM erhält: Thema/Anweisung, die zu generierenden Felder (Titel,
Inhalt, interne Zusammenfassung), die anderen Kapitel des Skripts
(inkl. ihrer internen Zusammenfassungen UND vorhandenen fig/eq-Labels
— für Notations- und Label-Konsistenz) und die noch nicht im Skript
verwendeten Medien des Kurses.
Es liefert ein JSON-Objekt mit EXAKT den angeforderten Schlüsseln.
"""

SCRIPT_SECTION_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Professor. Du sollst ein Kapitel eines
Vorlesungsskripts erstellen bzw. bestehende Felder eines Kapitels
ändern/verbessern.

KURS: {{ course_name }}
THEMA / ANWEISUNG DES TUTORS:
{{ topic }}

ANZUFORDERNDE FELDER — gib als Antwort ein gültiges JSON-Objekt mit EXAKT diesen Schlüsseln:
{{ generate_list }}

Mögliche Schlüssel und deren Bedeutung:
- "title": Kurzer, prägnanter Kapiteltitel (z.B. „Kapitel 3: Rekursion")
- "content": Vollständiger Markdown-Inhalt des Kapitels
- "summary": Interne Zusammenfassung des Kapitels in 3-6 Sätzen: zentrale Begriffe, verwendete Notation (Symbole, Schreibweisen), wichtige Definitionen/Sätze. Nimm AUCH alle wichtigen fig/eq-Labels des Kapitels mit auf (z.B. „Hauptformel: eq:shannon; Verteilungsdiagramm: fig:entropie“), damit spätere Kapitel und Übungsaufgaben darauf referenzieren können! Sie dient NUR der internen Konsistenz zwischen den Kapiteln und wird den Studenten NICHT angezeigt.

Keine weiteren Schlüssel, keine zusätzlichen Texte, keine Code-Blöcke (```json ... ```).
Achte dabei auf korrektes Escaping von special Characters. In Latex-Umgebungen muss insbesondere der Backslash escaped werden (z.B. $\\text{...}$ oder $$A \\rightarrow B$$). Dollar-Zeichen außerhalb von Code-Blöcken, die kein Latex triggern sollen, können mit Backslash \\$ escaped werden.
{% if other_chapters %}

ANDERE KAPITEL DIESER SKRIPTS (mit ihren internen Zusammenfassungen):
{% for ch in other_chapters %}
- {{ ch.title }}{% if ch.summary %} — {{ ch.summary }}{% endif %}{% if ch.labels %} | Labels: {{ ch.labels | join(", ") }}{% endif %}
{% endfor %}
Halte die Notation, Schreibweisen und Begriffswahl konsistent mit den anderen Kapiteln (z.B. gleiche Symbole für gleiche Größen), wo dies sinnvoll ist.
{% endif %}
{% if unused_media %}

NOCH NICHT IM SKRIPT VERWENDETEN MEDIEN DES KURSES (Titel — Beschreibung | Einbindung-Snippet):
{% for m in unused_media %}
- {{ m.title }}{% if m.description %} — {{ m.description }}{% endif %} | ![{{ m.title }}]({{ m.url }})
{% endfor %}
{% endif %}
{% if current_title %}

BESTEHENDER TITEL:
{{ current_title }}
{% endif %}
{% if current_content %}

BESTEHENDER INHALT:
{{ current_content }}
{% endif %}

Regeln:
- Generiere NUR die oben angeforderten Felder. Nicht angeforderte Schlüssel dürfen in der Antwort NICHT vorkommen.
- Das Kapitel ist reiner Vorlesungsinhalt: KEINE Übungsaufgaben, keine Aufgabenlisten und keine Aufgabenformulierungen (z.B. „Bestimme …“, „Zeige …“, „Beweise …“) — Übungsaufgaben werden gesondert im Kurs gepflegt und gehören NICHT ins Skript.
- Falls für ein angefordertes Feld bereits ein Inhalt existiert (s. o.), überarbeite/verbessere ihn gemäß der Anweisung — gestalte das Kapitel nicht grundlos neu, sondern behalte die Struktur bei, soweit die Anweisung nichts anderes vorschreibt.
- Falls kein Inhalt existiert, erstelle das Kapitel neu passend zum Thema.
- Der Inhalt ist Markdown für ein Vorlesungsskript: lehrbuchartige, präzise und strukturierte Darstellung (Definitionen, Sätze, Beweisskizzen, Beispiele, Übungshinweise) auf dem Niveau einer Universität.
- Beginne den Inhalt NICHT mit einer H1-Überschrift (der Kapiteltitel wird separat angezeigt); verwende ## für Abschnitte und ### für Unterabschnitte.
- Verwende $...$ für Inline-Math und $$...$$ für Display-Math.
- Medien: Bereits vorhandene /media/-Referenzen im bestehenden Inhalt unbedingt beibehalten. Zusätzlich DARFST du Medien aus der obigen Liste „noch nicht verwendet“ einbinden, wenn sie inhaltlich wirklich zum Kapitel passen (max. 1-2 pro Kapitel) — verwende dafür exakt den angegebenen /media/-Pfad. Erfinde KEINE anderen Medien-Pfade.
- Ein eingebundenes Medium IMMER auch im Fließtext per @fig:-Label referenzieren (nicht nur einbinden, sondern z.B. „wie in @fig:entropie dargestellt“), damit die Abbildung nummeriert und verlinkt wird.
- Nummerierung & Querverweise (wie in LaTeX):
{% raw %}
  - Abbildung, auf die du im Text Bezug nehmen möchtest: Snippet um ein Label ergänzen, z.B.
    ![Entropieverteilung](/media/1/abc.png){#fig:entropie}  →  wird als „Abb. N: Entropieverteilung“ gerendert.
  - Formel, auf die du Bezug nehmen möchtest: Label direkt nach dem Display-Math, z.B.
    $$H(X) = -\\sum_i p_i \\log_2 p_i$$ {#eq:shannon}  →  wird als „(N)“ neben der Formel gerendert.
  - Bezugnahme im Fließtext: @fig:entropie bzw. @eq:shannon → wird durch die klickbare Referenz („Abb. N“ bzw. „Gl. N“) ersetzt.
{% endraw %}
  - Beschrifte alle Objekte, auf die du im Text Bezug nimmst, UND wichtige Definitionen, Sätze und Formeln — auch ohne unmittelbare Bezugnahme im Text, damit sie in späteren Kapiteln und Übungsaufgaben referenziert werden können. Labels klein, snake_case, eindeutig im GESAMTEN Skript (siehe die „Labels“ bei den anderen Kapiteln — benutze bereits vorhandene Labels nicht neu und erfinde keine Labels, die dort bereits vergeben sind).
  - Querverweise auf Abbildungen/Gleichungen in ANDEREN Kapiteln funktionieren genauso: Verwende dafür die unter den anderen Kapiteln gelisteten Labels (z.B. @fig:entropie).
  - Alle wichtigen Labels müssen in der Zusammenfassung ("summary") vorkommen, damit sie später referenziert werden können. Erfinde aber auch keine Labels, die nicht im Inhalt vorkommen.
- Wenn Graphen zur Beschreibung benötigt werden: Verwende Mermaid (```mermaid ... ```) in Markdown.
  Wichtig: Knotentexte mit Sonderzeichen (z.B. runde Klammern oder <, > in Formeln) MÜSSEN in doppelte
  Anführungszeichen gesetzt werden: z.B. C["H(X) = log2(n)"] (NICHT C[H(X) = log2(n)]).
- Der Inhalt soll für sich allein lesbar sein (Kurzeinführung, Bezug zum Thema), aber sich auf das Kapitel beschränken.
"""
