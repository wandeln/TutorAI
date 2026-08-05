"""
Prompt-Templates für Kurs-Performance-Reports.

Wird vom LLM genutzt, um einen detaillierten Markdown-Report über die
Performance der Studierenden bei den gefilterten Aufgaben zu erstellen.

Verwendet __PLACEHOLDER__-Syntax statt {} um Konflikte mit geschweiften
Klammern im Prompt-Text zu vermeiden.
"""

COURSE_REPORT_PROMPT_TEMPLATE = """\
Du bist ein erfahrenes LLM, das als Dozent einen detaillierten Kurs-Report erstellt.

KURSNAME: __COURSE_NAME__

DEINE AUFGABE:
Analysiere die unten stehenden Daten und erstelle einen strukturierten Report in
Markdown-Format. Beantworte die folgenden Fragen:

1. **Overall Performance** — Wie läuft der Kurs insgesamt bei den ausgewählten Aufgaben?
2. **Gut gelaufene Aufgaben** — Welche Aufgaben liefen gut? Welche haben Probleme verursacht?
3. **Hinweise / Tips** — Bei welchen Aufgaben haben Studierende Hinweise benötigt?
4. **Zeit / Versuche** — Bei welchen Aufgaben haben Studierende sehr viel Zeit oder Versuche benötigt?
5. **Studenten-Einschätzung** — Wer ist gut dabei? Wer braucht Hilfe? Wer ist abgesprungen?
6. **Vorlesungs-Empfehlungen** — Welche Themen sollten nochmal in der Vorlesung behandelt werden? Wie erklären?
7. **Zusätzliche Übungen** — Zu welchen Themen sollte man nochmal Übungsaufgaben erstellen?

AUFGABEN:
__TASKS_DATA__

STUDENTEN-DATEN:
__STUDENTS_DATA__

ERWARTETES REPORT-FORMAT (Markdown):

# Kurs-Report: __COURSE_NAME__

## Overall Performance

(Zusammenfassende Einschätzung)

## Aufgaben-Analyse

### Gut gelaufene Aufgaben

(Welche Aufgaben, warum)

### Problematische Aufgaben

(Welche Aufgaben, welche Probleme, welche Hinweise wurden benötigt)

### Zeit- und Versuchs-Statistik

(Welche Aufgaben benötigten viel Zeit/Versuche)

## Studenten-Einschätzung

### Gut dabei

(Liste der leistungsstarken Studierenden)

### Brauchen Unterstützung

(Liste der Studierenden, die Hilfe benötigen)

### Abgesprungen / Keine Einreichungen

(Liste der inaktiven Studierenden)

## Empfehlungen für die Vorlesung

(Themen zur Wiederholung, Erklärungsvorschläge)

## Empfehlung für zusätzliche Übungen

(Themen, zu denen zusätzliche Aufgaben sinnvoll wären)

RICHTLINIEN:
- Schreibe auf Deutsch, professionell und konstruktiv
- Verwende Markdown-Formatierung (**fett**, - Listen, ## Überschriften, Tabellen)
- Beziehe dich auf konkrete Daten aus den unten stehenden Informationen
- Sei ehrlich und direkt — keine Floskeln
- Wenn ein Student keine Einreichungen hat, erwähne das explizit
- Nutze ```code```-Blöcke nur bei Code-Beispielen
"""