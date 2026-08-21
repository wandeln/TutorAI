"""
Prompt-Templates für LLM-gestützte Code-Aufgaben.

Die Musterlösung wird gemeinsam mit Titel/Aufgabenstellung über das
vereinheitlichte UNIFIED_TASK_PROMPT_TEMPLATE (creation_prompt.py) generiert.
"""


# ══════════════════════════════════════════════════════════════════
# Code-Aufgabe: Code-Vorlage + Unit-Tests (2. Schritt nach Musterlösung)
# ══════════════════════════════════════════════════════════════════

CODE_TEMPLATE_TESTS_PROMPT_TEMPLATE = """\
Du bist ein erfahrener Tutor und Software-Entwickler. Für die folgende Code-Aufgabe hast du
bereits die korrekte Musterlösung. Jetzt sollst du daraus zwei Dinge ableiten:

1. **Code-Vorlage** (code_template): Ein Gerüst/Scaffold für den Studenten. Funktionen/Classen
   sind deklariert, aber die Bodies sind leer (z.B. nur `pass` oder `# TODO`). Der Student ergänzt den Code oder Kommentare für Teilaufgaben, die Text benötigen. Zudem sollte die Vorlage, wenn es sich anbietet, auch schöne Visualisierungen mit matplotlib enthalten, welche die Lösung des Studenten veranschaulichen. Diese visualisierungen sollten mit plt.show() bei Aufruf des Template Scripts angezeigt werden.
2. **Public Tests** (public_tests): Einheitstests als Python unittest-Code, die der Student sieht.
   Nutze eine Klasse `PublicTest(unittest.TestCase)`. Teste grundlegende, normale Fälle (ca. 3-5 Tests). Liefere hilfreiche assertion Hinweise für die Studenten in den assert calls.
3. **Private Tests** (private_tests): Zusätzliche verborgene Unit-Tests als Python unittest-Code.
   Nutze eine Klasse `PrivateTest(unittest.TestCase)`. Teste Edge-Cases, Grenzwerte, Fehlerfälle (ca. 3-5 Tests).

AUFGABENSTELLUNG:
__DESCRIPTION__

MUSTERLÖSUNG:
__MODEL_SOLUTION__

Gib deine Antwort als gültiges JSON-Objekt mit folgenden Schlüsseln:
{
  "code_template": "...",      // Scaffold für den Studenten (aus Musterlösung abgeleitet) - evtl mit zusätzlichen matplotlib visualisierungen, welche bei Aufruf des code template scripts aufgerufen werden
  "public_tests": "...",       // Public unittest-Code (inkl. `import unittest`). Liefere hilfreiche assertion messages für die Studenten in den assert calls (z.B. self.assertEqual(traverse_inorder(None), [], "Hast du den Fall root=None korrekt berücksichtigt?"))
  "private_tests": "..."       // Private unittest-Code (inkl. `import unittest`)
}
Achte dabei auf korrektes Escaping von special Characters. In Latex-Umgebungen muss insbesondere der Backslash escaped werden (z.B. $\\text{...}$ oder $$A \\rightarrow B$$). Dollar-Zeichen außerhalb von Code-Blöcken, die kein Latex triggern sollen können mit Backslash \\$ escaped werden.

Regeln:
- Die `code_template` MUSS zur `model_solution` passen — gleiche Signaturen, gleiche Struktur, nur ohne Implementierung.
- Die Tests MÜSSEN die Funktionen/Klassen aus der Aufgabenstellung aufrufen (nicht aus der Lösung).
- Die Tests müssen so geschrieben sein, dass die Musterlösung ALLE Tests besteht.
- Nutze `self.assertEqual()`, `self.assertTrue()`, `self.assertRaises()` etc.
- Public-Tests testen grundlegende Fälle, Private-Tests testen Edge-Cases und Grenzwerte.
- Antworte NUR mit JSON, keine zusätzlichen Texte, keine Code-Blöcke (```json ... ```).
"""
