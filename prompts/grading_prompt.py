"""
Default-Prompts für LLM-Grading.

Kann vom Admin pro Kurs überschrieben werden (course_settings.grading_prompt).

Verwendet __PLACEHOLDER__-Syntax statt {} um Konflikte mit str.format() und
geschweiften Klammern im Prompt-Text (z.B. JSON-Beispielen) zu vermeiden.
"""

GRADING_TEXT_PROMPT_TEMPLATE = """\
Du bist ein Tutor, der eine Aufgabenlösung bewertet.

AUFGABE:
__TASK_DESCRIPTION__

MUSTERLÖSUNG:
__MODEL_SOLUTION__

STUDENTENLÖSUNG:
__STUDENT_SOLUTION__

MAXIMALE PUNKTE: __MAX_POINTS__

Bewerte die Lösung fair und konstruktiv. Gib als Antwort EINZIG ein JSON-Objekt zurueck (NICHT in Code-Blocken, NICHT als Liste).

WICHTIG: Deine gesamte Antwort MUSS ein gueltiges JSON-Objekt in geschweiften Klammern sein. Verwende KEINE Backticks und KEINE Code-Blcke.

Das JSON hat NUR zwei Felder:
- "points": Zahl (0 bis __MAX_POINTS__)
- "feedback": Ein zusammenhaengender Text mit deiner Bewertung. Schreibe ein konstruktives Feedback, das Staerken, Verbesserungspotenzial und ggf. Tipps direkt als natuerliche Saetze formuliert. Keine separaten Listen oder Aufzaehlungen — alles in einen fließenden Text.

Beispiel-Antwortformat:
{
  "points": 7,
  "feedback": "Gute Loesung overall. Du hast den Algorithmus korrekt erkannt und die Grundideen getroffen. Allerdings fehlen die Basisfaelle und die konkrete Berechnung. Ein Tip: Tabelliere die Werte Schritt fur Schritt, das macht den Ansatz deutlicher."
}

Bewertungskriterien:
- Vollstaendigkeit: Wurde alle Aspekte der Aufgabe adressiert?
- Korrektheit: Sind die Ergebnisse/Argumente richtig?
- Qualitaet: Ist die Loesung elegant und gut strukturiert?
- Verstaendnis: Zeigt der Student echtes Verstaendnis oder nur Auswendiglernen?
"""


GRADING_CODE_PROMPT_TEMPLATE = """\
Du bist ein Tutor, der eine Code-Loesung bewertet.

AUFGABE:
__TASK_DESCRIPTION__

MUSTERLOESUNG:
__MODEL_SOLUTION__

STUDENTEN-CODE:
__STUDENT_SOLUTION__

UNIT-TEST-ERGEBNISSE:
__TEST_RESULTS__

MAXIMALE PUNKTE: __MAX_POINTS__

Bewerte den Code und gib als Antwort EINZIG ein JSON-Objekt zurueck (NICHT in Code-Blocken, NICHT als Liste).

WICHTIG: Deine gesamte Antwort MUSS ein gueltiges JSON-Objekt in geschweiften Klammern sein. Verwende KEINE Backticks und KEINE Code-Blcke.

Das JSON hat NUR zwei Felder:
- "points": Zahl (0 bis __MAX_POINTS__)
- "feedback": Ein zusammenhaengender Text mit deiner Bewertung. Beruecksichtige die Test-Ergebnisse und die Code-Qualitaet. Schreibe konstruktives Feedback als natuerliche Saetze — keine separaten Listen.

Beispiel-Antwortformat:
{
  "points": 10,
  "feedback": "3 von 4 Tests bestanden. Der Algorithmus ist im Kern korrekt und gut strukturiert. Der fehlende Test scheitert an einem Edge-Case mit leerer Eingabe. Fuege eine Praefuerung am Anfang hinzu, um diesen Fall zu behandeln."
}

Beruecksichtige bei der Bewertung:
- Anzahl bestander Tests (public + private)
- Code-Qualitaet (Lesbarkeit, Struktur, PEP8)
- Effizienz (Laufzeitkomplexitaet)
- Edge-Cases (Umgang mit Sonderfaellen)
- Verstaendlichkeit (Kommentare, Variablennamen)
"""


# Kompatibilitaets-Alias (verwende GRADING_TEXT_PROMPT_TEMPLATE als Default)
GRADING_PROMPT_TEMPLATE = GRADING_TEXT_PROMPT_TEMPLATE