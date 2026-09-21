# verify.sh-Muster für Workspace-Presets

Das `verify`-Feld der Workspace-Spec gibt den Befehl an, der im
**Grading-Container** ausgeführt wird (nach Abgabe, in einem frischen
Container mit Student-Snapshot + privaten Dateien). Für die Presets
`cpp`, `asm` und `server` ist der Standard-Verifizierer

```
bash .solution/verify.sh
```

— ein Skript, das der Tutor als **private Datei** unter `.solution/`
hinterlegt. Der exit-Code entscheidet: `0` = bestanden, sonst
fehlgeschlagen. Der Output (stdout/stderr) fließt in den LLM-Grading-Prompt.

**Wichtig:** `.solution/verify.sh` ist *privat* — Studenten sehen es nie
(weder im Workspace, im Snapshot-Download noch im LLM-Grading-Kontext als
"Test", es kommt nur als *Musterlösung*-Material in den Prompt).

---

## Server-Preset: Probing-Muster

Dienste starten im Container **nicht** automatisch (kein Init). Das
Muster: verify.sh startet den Dienst selbst, prüft per Probing,
killt dann wieder. So ist die Prüfung deterministisch und idempotent,
unabhängig davon, was der Student in seinem Workspace zuletzt gemacht hat.

Beispiel-Aufgabe *"nginx mit Custom-Errorseite auf Port 8080"*:

```bash
#!/bin/bash
# .solution/verify.sh — wird im Grading-Container mit cwd=/workspace ausgeführt
set -u
PORT=8080
FAIL=0

# 1) Konfiguration des Studenten übernehmen (falls existiert)
if [ -f nginx.conf ]; then
  cp nginx.conf /etc/nginx/nginx.conf
fi
# Site-Config aus dem Workspace installieren (typisches Übungs-Setup)
if [ -f site.conf ]; then
  mkdir -p /etc/nginx/sites-enabled
  cp site.conf /etc/nginx/sites-enabled/default
fi
[ -d html ] && cp -r html/* /var/www/html/ 2>/dev/null

# 2) Dienst starten
service nginx start
sleep 1

# 3) Probing
if curl -sf "http://127.0.0.1:${PORT}/" -o /tmp/page.html; then
  echo "OK: HTTP-Antwort auf Port ${PORT}"
  grep -qi "welcome\|<h1" /tmp/page.html && echo "OK: Inhalt plausibel"
else
  echo "FEHLER: nginx antwortet nicht auf Port ${PORT}"
  FAIL=1
fi

# 4) Optional: weitere Probes (Beispiele)
# ss -ltnp | grep -q ":8080" && echo "OK: Port 8080 gelistet"
# dig +short @127.0.0.1 beispiel.localhost | grep -q "127.0.0.1" && echo "OK: DNS"

# 5) Aufräumen
service nginx stop >/dev/null 2>&1
exit $FAIL
```

Beispiel *"SSH-Server mit Port-Wechsel + Key-Auth"*:

```bash
#!/bin/bash
set -u
FAIL=0
# Studenten-Konfiguration anwenden
[ -f /workspace/sshd_config ] && cp /workspace/sshd_config /etc/ssh/sshd_config
service ssh start
sleep 1
ss -ltn | grep -q ":2222 " && echo "OK: SSH auf Port 2222" || { echo "FEHLER: Port 2222 nicht offen"; FAIL=1; }
service ssh stop >/dev/null 2>&1
exit $FAIL
```

**Best Practices:**

- `set -u` ja, `set -e` nein — man will alle Probes laufen lassen und
  den kumulierten Status melden (mehr Signal für LLM + Tutor).
- Immer stoppen/cleanen am Ende (Grading-Container wird danach gelöscht,
  aber saubere Logs helfen beim Debugging).
- Probing gegen `127.0.0.1` (der Container hat sein eigenes Loopback-Netz;
  `--network=none`-Tasks testen nur lokale Ports).
- Bei `internet: true` dürfen Probes auch externe Endpunkte prüfen.

## C++-Preset

```bash
#!/bin/bash
set -u
# Binary bauen (wie der Run-Befehl, oder Makefile)
if [ -f Makefile ]; then make -s solution; else g++ -O2 -std=c++17 -o solution main.cpp; fi
./solution > /tmp/out.txt 2>&1
RC=$?
[ $RC -eq 0 ] || { echo "FEHLER: Exit-Code $RC"; exit 1; }
# Ausgabeprobes:
grep -q "expected-output-1" /tmp/out.txt || { echo "FEHLER: Zeile 1 fehlt"; exit 1; }
echo "OK"
```

## C++-Preset (Multi-File, Preset `cpp-mk`)

Für C++-Projekte mit mehreren Quellen und Makefile (Preset `cpp-mk`,
Run-Befehl `make`). Starter-Dateien des Tutors: `Makefile` (baut z.B.
`solution` aus `main.cpp` + `lib.cpp`), `main.cpp`, `lib.cpp`.

```bash
#!/bin/bash
set -u
# Makefile des Studenten nutzen — frischer Build aus dem Student-Code
make -s || { echo "FEHLER: Build fehlgeschlagen"; exit 1; }
./solution > /tmp/out.txt 2>&1
RC=$?
[ $RC -eq 0 ] || { echo "FEHLER: Exit-Code $RC"; exit 1; }
# Ausgabeprobes:
grep -q "expected-output-1" /tmp/out.txt || { echo "FEHLER: Zeile 1 fehlt"; exit 1; }
echo "OK"
```

Der Build ist kurz (kein long-running), am Ende wird nichts gestoppt —
der Grading-Container wird danach einfach gelöscht.

## Assembler-Preset

```bash
#!/bin/bash
set -u
nasm -f elf64 main.asm -o main.o || { echo "FEHLER: NASM"; exit 1; }
ld -o solution main.o || { echo "FEHLER: Linker"; exit 1; }
./solution > /tmp/out.txt 2>&1 || { echo "FEHLER: Laufzeit"; exit 1; }
grep -q "42" /tmp/out.txt && echo "OK: Ausgabe enthält 42"
```

## python-ml-Preset (Vergleich)

Hier ist der Standard `python -m pytest .tests/ -v` — die Tests liegen
unter `.tests/` (ebenfalls privat) und prüfen typischerweise nur
Artefakte aus `/workspace` (s. `scripts/create_workspace_example.py`).
