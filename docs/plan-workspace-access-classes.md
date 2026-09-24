# Workspace-Zugriffsklassen (Refactor Pfad-Zonen → explizite Klassen)

Stand: 2026-09-23 · Status: Phasen A–D + Init-Image-Skip + Skript-Renamen/Stubs/🧪-Testlauf + Grading-Test-Ergebnisse fertig + deployed

## Status (pro Phase)

- [x] **Phase A** — Datenmodell + UI (deployed, User getestet)
- [x] **Phase B** — Agent-Mounts (deployed mit C)
- [x] **Phase C** — Init 2-Phasen + Init-Artefakt-Platzierung (deployed; 2-Phasen-Log + [init]-Einträge vom User zu testen)
- [x] **Phase D** — Aufräumen (path_zone, compose_gen, LLM-Prompt, Doku) — deployed; Download-Paket funktional getestet (Task 37)
- [x] **Optimierung** — Init-Image-Skip (deployed, vom User zu testen; s. Sektion unten)
- [x] **Bugfix** — Init-Manifest = IST-Zustand (statt Build-Diff) + Tree-Auto-Refresh (deployed, vom User zu testen; s. Sektion unten)
- [x] **Skript-Runde** — Renamen (`.init_hidden.sh`, `test.sh`, `.test_private.sh`, `.run_solution.sh`) + Stubs + 🧪 Musterlösung-Testlauf (deployed; Task 37 migriert, vom User zu testen; s. Sektion unten)
- [x] **Bugfix** — Sync-Purge vs. Init-Build-Race (stille Datenverluste) (deployed; Task 38 repariert, vom User zu testen; s. Sektion unten)
- [x] **Bugfix** — `__pycache__`-False-Positives im Init-Image-Skip-Filter (deployed; s. Sektion unten)
- [x] **Artefakt-Sammlung entfernt** — `workspace_artifacts` restlos raus (deployed; s. Sektion unten)
- [x] **Runde 2026-09-23** — Grading mit Test-Ergebnissen (public + private) + ro-Paket-Standardverhalten + optionale System-Skripte (deployed; vom User zu testen; s. Sektion unten)
- [x] **Runde 2026-09-24** — Rename `.run_solution.sh` → `.test_solution.sh` + robuster Testlauf-Stub (dev-deployed; vom User zu testen; s. Sektion unten)

## Ziel

Workspace-Aufgaben bekommen **explizite, im Datei-Baum sichtbare
Zugriffsklassen** statt der Pfad-Konvention (`path_zone()`). Endzustand:

1. **3 Zugriffsklassen, explizit pro Datei UND Ordner** (Rechtsklick in der
   Tutor-Tree), direkt im Baum sichtbar:
   - ✏️ **Edit** (Default) — Tutor + Student schreiben
   - 🔒 **Read-only** — Student nur lesen; **shared** (1 Kopie auf dem
     Agenten, ro-Bind-Mount in jedem Student-Container)
   - 👤 **Hidden** — Student sieht es nie; nur Tutor + Grading-Injection
2. **Erbungs-Regel:** effektive Klasse = die **restriktivste** von
   (eigene explizite Klasse, alle Ordner-Vorfahren). Restriktivität:
   `hidden > readonly > edit`. → keine Validierung nötig; eine Ordner-
   Setzung wirkt automatisch auf alle Kinder.
3. **Student-Container = Tutor-Baum abzüglich 👤, auf denselben Pfaden:**
   `/workspace`-Named-Volume (✏️-Bereich) + je 🔒-Pfad ein ro-Bind-Mount
   aus dem geteilten Agent-Verzeichnis. Der `/assets`-Mount fällt weg.
   Ordner-Namen (`data/`, `models/`, …) sind frei — nichts Hardkodiertes.
4. **Init 2-Phasen:**
   - `init.sh` (public): darf in 🔒 + ✏️ schreiben, **NICHT** in 👤
     (Mount-Enforcement: 👤-Pfade sind in Phase 1 gar nicht gemountet).
   - `init_private.sh` (👤-Datei): Phase 2, läuft aus dem Phase-1-Commit,
     Supermenge (darf auch in 👤 schreiben, z. B. private Testsets laden).
     Nie im Student-Paket, nie in der Student-View.
   - Gemeinsames 30-min-Timeout, gemeinsames Live-Log (Trennlinie
     `── init_private.sh ──`).
5. **Init-Artefakt-Platzierung = Ziel-Klasse entscheidet:**
   - 🔒-Ziel → shared auf dem Agenten (1 Kopie für alle Studenten)
   - ✏️-Ziel → Seed-Store (Agent), beim frischen Student-Volume per
     docker-cp reingekopiert
   - 👤-Ziel (nur init_private.sh) → private Agenten-Region, nur für
     Grading-Injection
   - Das Task-Image ist **reine Umgebung** (Base + Pakete), enthält keine
     Task-Dateien und wird an Studenten nie ausgeliefert.
   - **Ein Manifest** (`asset_dir/.init_manifest.json`, Generalisierung von
     `data/.init_manifest`) trackt alle Init-Artefakte inkl. Scope →
     Re-Init-Cleanup + Purge-Schutz.
6. **Download-Paket bleibt klein:** Dockerfile (public Base + Pakete) +
   public Dateien (✏️+🔒, OHNE Init-Artefakte) + Skripte. Init-Artefakte
   regeneriert der Student lokal per init.sh. Tutoren-Paket = alles
   inkl. 👤.
7. **Grading:** alle 👤-Dateien werden injiziert (die
   `.solution/`/`.tests/`-Naming-Magie fällt weg); `test_private.sh`
   (eine 👤-Datei, Pfad frei) ist der authoritative Judge.
8. **Tutor sieht Init-Artefakte** (aus dem Agent-Manifest) mit **[init]-
   Badge, read-only** (kein Edit/Delete/Move).
9. **Kein Legacy-Fallback.** `path_zone()`, die `image-workspace/`-Zone,
   `/assets`, die 5-Zonen-Legende werden restlos entfernt.

## Datenmodell

- `TaskWorkspaceFile.access: Optional[str]` (NULL = erben; Werte
  `readonly`/`hidden`; `edit` = NULL). `is_public`-Spalte wird dropped.
- Neue Tabelle `TaskWorkspaceFolder(task_id, path, access)` — nur Zeilen
  für `readonly`/`hidden` (explizites `edit` braucht keine Zeile).
  Empty-Folder werden erst bei Zugangsklassen-Setzung persistiert.
- Effektive Berechnung im Service (`effective_*_access`), nie im Agent.

## Phasen

### Phase A — Datenmodell + UI
- Model: `access` statt `is_public`; neue `TaskWorkspaceFolder`-Tabelle;
  `column_migrations`/`column_drops` in `database/base.py`.
- Einmaliges Skript `scripts/migrate_workspace_access.py`: Pfad-Konvention
  → Klassen (data/→🔒, tests/→🔒, run.sh/init.sh→🔒,
  .solution/.tests→👤, Rest ✏️) + init.sh: `/assets/data/` →
  `/workspace/data/`.
- `workspace_service.py`: effektive-Zugriff-Helper,
  `readonly_mount_paths`/`hidden_mount_paths`, Save/Move behalten
  explizite Klassen; `save_task_file(..., access=...)`.
- API: `list_workspace_files` liefert `access` (effektiv) + `file_access`
  (explizit) + `folders`-Array (effektiv + own); neue Routes
  `POST /workspace/access` ({path, is_folder, access}) und
  `POST /workspace/folders/move` ({src, dst}).
- `workspace.js`: `ZONES`/`zoneOf` → `ACCESS`-Klassen; Marker pro
  Datei+Ordner; Kontextmenü „Zugriff“ (Datei: erben/readonly/hidden,
  Ordner: edit/readonly/hidden); access-basierte Move-Gates (Student:
  nur edit→edit); `showZoneTargets` raus (Ordner sind im Baum).
- Templates: 3-Klassen-Legende, Configs (readOnlyCheck/moveGate).
- Student-API: Guards + `access`-Annotation in der File-Liste.
- Grading-Kontext: `solution_files`/`test_files` → `private_files`
  (alle 👤-Dateien); Judge-Skript = 👤-Datei `test_private.sh` (Pfad frei).

### Phase B — Agent-Mounts
- `sync_assets` (Backend): sync-t ALLE Dateien mit effektiver Klasse
  ≠ hidden + explicit Ordner-Pfade (leere Ordner werden angelegt).
- Agent-`assets_sync`: nimmt `folders` an; Purge behält Ordner +
  Manifest-Einträge.
- Spec: neuer Key `readonly_paths` (top-level 🔒-Pfade, files+folders).
- `_build_create_args`: statt `/assets`+tests/+Skript-Mounts → je
  🔒-Pfad ein ro-Bind-Mount `asset_dir/<path>` → `/workspace/<path>`.
- Mount-Layout-Hash als Container-Label `tutorai.mounts`; Mismatch →
  Container neu anlegen (Volume bleibt).
- Student-Write-Guards (Backend + Agent) auf effektive Zugriffe.

### Phase C — Init 2-Phasen
- `init_hash` = sha256(base_image + init.sh + init_private.sh +
  sortierte Access-Map)[:12] — Backend (`task_init_hash`) und
  Spec-Build synchron.
- Agent-`_do_init_build`:
  - Phase 1 (falls init.sh): Build-Container mit `/workspace` =
    Host-Temp-Dir (rw-Bind) + je 🔒-Pfad rw-Bind (shared); 👤 NICHT
    gemountet. init.sh via docker-cp.
  - Phase 2 (falls init_private.sh): Container aus Phase-1-Commit +
    je 👤-Pfad rw-Bind (private Region `asset_dir/.private/`).
  - Commit = Phase 2 (oder 1). Gemeinsames 30-min-Timeout, gemeinsames
    Live-Log mit Trennlinie.
  - Artefakte: 🔒-Schreiber landen schon im shared-Dir; ✏️-Schreiber
    (Temp-Dir-Diff) → Seed-Store; 👤-Schreiber → private Region.
    Manifest `.init_manifest.json` {shared, seed, private}.
  - Re-Init/Purge/Stop räumt Manifest-Dateien aus.
- `init_private.sh` kommt per Init-Request-Body (b64), wird in der
  privaten Region persistiert, nie gemountet im Student-Container.
- Friesche Student-Volumes: Starter-Dateien = Tutor-✏️-Dateien +
  Seed-Artefakte (Agent ergänzt Seeds beim workspace_create).
- Grading: Backend holt 👤-Init-Artefakte vom Agenten
  (`init_artifacts?scope=private`) und injiziert sie.
- Tutor-File-Liste: Manifest-Merge (shared+seed+private) als virtuelle
  read-only [init]-Einträge (ersetzt die image-workspace/ -Merge);
  Write-Endpoints blocken [init]-Pfade (403).

### Phase D — Aufräumen ✅ fertig
- `path_zone()`/`is_public_path` + alle Call-Sites entfernen.
- `/assets`-Mounts entfernen. (`/assets/{course}/{task}`-REST-Endpunkt +
  `safe_asset_path` bleiben: API-Vertrag mit dem Agenten / generischer
  Path-Sanitizer — bewusst NICHT umbenannt.)
- `image-workspace/`-Zone komplett: Agent-Funktionen
  (`image_workspace_*`), Agent-Routes, Client-Methoden,
  Backend-Helpers (`_image_workspace_files`, `_read_image_workspace_file`,
  `_reject_image_zone`), Route `/workspace/image-workspace`,
  JS-`image`-Zone, Template-Buttons/Config.
- Disk-Layout vereinheitlichen: `.private/`-Prefix wegräumen
  (Hidden-Dateien liegen unter normalem Pfad), `file_disk_path`
  vereinfachen (einmaliger Disk-Move in einem Skript).
- `compose_gen.py` (Download-Paket) auf effektive Klassen umstellen:
  Paket = Dockerfile + public Dateien (✏️+🔒, ohne Init-Artefakte) +
  Skripte; init-Service (Phase 1) + optional private-init (Phase 2, nur
  Tutor-Paket); ro-Mounts je 🔒-Pfad im lokalen Compose.
  Umsetzung: `workspace/` enthält ALLE public Dateien am realen Pfad;
  👤-Dateien (nur Tutor) am realen Pfad top-level; 🔒-Dateien (ro-Mounts)
  rw im init-Service; `verify`-Service (Tutor) mit ro-Mounts auf alle
  👤-Dateien, Command = Judge-Pfad aus der DB; 🔒-Cap `MAX_DATA_IN_PACKAGE`;
  Zugriffsklassen sind Teil des Cache-Fingerprints (`_source_fingerprint`).
- LLM-Prompt (workspace-generierung + grading) auf Zugriffsklassen
  umstellen: Dateien mit optionalem `access`, Ordner-Klassen als
  `folders`-Map; „private Testdaten → init_private.sh, nie init.sh“.
  Umsetzung: `workspace_task_prompt.py` (files + folders),
  `workspace_presets.validate_workspace_generation` (folders-Validierung),
  `api/tutor.py` ai-generate (folders via `set_folder_access`).
- Doku `docs/plan-workspace-tasks.md` + `workspace-verify-examples.md`
  aktualisieren; `create_workspace_example.py` anpassen.
  Umsetzung: `plan-workspace-tasks.md` als überholt markiert (Historie
  belassen), `create_workspace_example.py` legt Klassen an (run.sh 🔒 als
  Datei; data/tests 🔒, .tests/.solution 👤 als Ordner).

## Optimierung: Init-Image-Skip (kein Commit bei reinen Downloads)

**Ziel:** Wenn `init.sh`/`init_private.sh` das Image NICHT verändert
(z. B. nur Datei-Downloads in 🔒-Ordner), wird kein Task-Image
committet — die Studenten-Container laufen direkt auf dem Basis-Image,
ohne zusätzlichen Speicherplatz.

**Umgesetzt (2026-09-20, deployed):**
- Erkennung per **`docker diff`** auf dem Build-Container (bewusst NICHT
  per `init.sh`-Parsing — pip/apt-Heuristik zu fragil). Bind-Mount-Writes
  (der gesamte `/workspace`-Bereich) landen nicht in der Image-Schicht;
  Ephemere-Pfade (Paket-Caches `/root/.cache`, `/var/lib/apt`, `/var/log`,
  …) werden zusätzlich gefiltert (`_EPHEMERAL_IMAGE_PATHS`), sonst würde
  z. B. ein reines `apt-get update` einen Fast-Leer-Commit auslösen.
  Der Init-Container bekommt `--tmpfs /tmp` (wie der Student-Container).
- Nach jeder Phase: Commit nur bei nicht-leerem Diff (Diff-Fehler →
  defensiv Commit). Sonst wird die Ref per **`docker tag`** gesichert
  (kostenlos, geteilte Layer): Phase 2 ohne Änderungen → Tag auf dem
  Phasen-1-Image; gar keine Änderungen → Tag als Alias des Basis-Images.
  Die Ref existiert danach IMMER → Status-/Workspace-Logik
  (`image_exists(ref)`) läuft unverändert weiter — kein Manifest-Key,
  keine Status-Änderung nötig.
- `remove_task_images`: zusätzlich zum Label-Filter der Namensraum-Filter
  `reference=tutorai/task/{c}-{t}:*` — Alias-Tags auf dem Basis-Image
  vererben keine Task-Labels. `docker rmi` entfernt nur den Tag; das
  Basis-Image bleibt über seine eigenen Tags erhalten.
- Build-Log-Zeilen: „(Keine Image-Änderungen — Commit übersprungen.)“ /
  „(Keine Image-Änderungen — Task-Image ist ein Alias des
  Basis-Images.)“
- Verifiziert: `_image_changes`-Parser gegen echtes `docker diff`
  (Format `A`/`C`/`D` + Pfad), `remove_task_images` mit Fake-Tag.

## Bugfix: Init-Manifest = IST-Zustand (nicht Build-Diff)

**Problem:** Das Manifest listete früher nur die Schreib-Ergebnisse des
jeweiligen Einzel-Builds (`after − before`-Mengen-Diff). Eine Datei, die
auf Disk schon vorhanden war (Artefakt eines früheren Builds, z. B. nach
Access-Config-Wechsel), fiel aus dem Manifest → [init]-Eintrag im
Tutor-Baum verschwunden (die Datei blieb auf Disk); bei `shared` drohte
der Sync-Purge, das alte Artefakt zu löschen, und bei `seed` wanderten
Geist-Dateien ohne Manifest-Eintrag weiterhin in frische Student-Volumes.

**Fix (2026-09-21, deployed):**
- `private` = **Voll-Snapshot** von `.private/` (ohne `init_private.sh`) —
  die private Region nimmt ausschließlich Init-Artefakte auf.
- `shared` = alte Manifest-Einträge, die noch auf Disk existieren,
  + neue Schreib-Ergebnisse (das Asset-Dir hält daneben die sync-ten
  User-Dateien → kein Voll-Snapshot möglich).
- `seed` = wie bisher das komplette Build-Ergebnis (frische Temp-Dir)
  + **Purge von `.seeds/`**: alte Seed-Dateien ohne Manifest-Eintrag
  werden gelöscht (Store = exakt aktueller Build → frische Volumes
  konsistent mit dem Baum).
- Einmal-Reparatur bestehender Manifests:
  `scripts/repair_init_manifests.py` (`--dry-run`, idempotent) —
  gelaufen für Task 12/37 (`.solution/bla2.txt` sichtbar gemacht,
  `.seeds` von 5 alten Dateien bereinigt).
- Tutor-Tree: nach beendetem Init-Build (building → ready/failed/none)
  automatisch `wsUI.refresh()` (zuvor nur nach Seite-Reload).

## Bugfix: Sync-Purge vs. Init-Build-Race (stille Datenverluste)

**Problem (2026-09-21, Task 12/38 MNIST):** Das UI-Polling-Loop sendet
alle paar Sekunden `POST /assets` (Sync mit `delete_missing`) +
`POST /init-build`. Der Sync-Purge (`purge_missing_assets`) schützt nur
Dateien, die bereits im Init-Manifest stehen — das Manifest wird aber
erst am ENDE des Builds geschrieben. Während des Builds (also die ganze
Build-Dauer!) konnte der Purge daher:

1. **laufende Downloads löschen:** Der tote torchvision-Mirror 1
   (yann.lecun.com) braucht Sekunden, bis die Datei des 2. Mirrors
   geschrieben wird; in diesem Fenster rasiert der Purge das noch LEERE
   `data/MNIST/raw/`-Verzeichnis (leere Ordner werden aufgeräumt) →
   `open('wb')` des 2. Mirrors → `FileNotFoundError` → Build-Fehler
   (in der UI sichtbar, User-Report).
2. **fertige Artefakte löschen:** Das letzte Polling-Sync 33 ms nach dem
   Commit (vor dem Manifest-Write) löschte die heruntergeladenen Dateien
   → Build meldete `ready`, aber `data/` und Manifest waren leer →
   Studenten-Container bekamen einen leeren ro-Mount. Stille Datenverlust.

**Fix (2026-09-21, deployed):**
- Neues `docker_ops.init_build_running(course, task)` (INIT_BUILDS,
  Hash-agnostisch). `assets_sync` **überspringt den Purge** bei aktivem
  Build (Log-Zeile; die Datei-Weiterleitung läuft weiter) — das nächste
  Sync nach dem Build räumt dann auf, wenn das Manifest steht.
- Defense-in-Depth: `_do_init_build` stellt sicher, dass die
  Mount-Quellen aller 🔒-Top-Level-Pfade auf dem Host existieren
  (fehlende Pfade werden als Ordner angelegt) — sonst würde der rw-Mount
   stillschweigend weggelassen und init.sh in die Temp-Dir schreiben
   (Artefakt-Verlust beim Build-Cleanup, kein Manifest-Eintrag).
- Build-Log: `\r`-Progress-Updates komprimiert (`_compact_cr_log`,
   nur Endstand je Zeile) + Phasen-Trennlinie `── .init_hidden.sh ──`
   auch im gespeicherten Log-Tail (zuvor nur im Live-Log).

**Task 12/38-Reparatur:** `docker rmi` der verwaisten Images, leere
`.private/`-Mount-Stumps (Verzeichnisse `.run_solution.sh/`,
`.test_private.sh/`) entfernt, Re-Init per `workspace_service.init_build`
→ Daten + Manifest wieder intakt, Task-Image = Alias des Basis-Images
(s. nächste Sektion).

## Bugfix: `__pycache__`-False-Positives im Init-Image-Skip-Filter

**Problem (2026-09-21, Task 12/38):** `python -c "from torchvision
import datasets"` erzeugt ~160 `__pycache__`/`.pyc`-Änderungen, obwohl
das Image sonst unberührt bleibt. Der Filter (s. Init-Image-Skip) erkannte
das nicht:

- Neu ANGELEGTEN `__pycache__`-Verzeichnisse erscheinen als
  `A /…/__pycache__` (ohne Trailing Slash) → Test `"/__pycache__/" in p`
  fehl.
- docker diff markiert die komplette **Vorfahren-Kette** geänderter Pfade
  als `C` (verändert) (`C /usr`, `C /usr/local`, …) → der Diff wurde nie
  leer, selbst ohne eine einzige echte Datei-Änderung.

Folge: Ein reiner Download-Init produzierte einen kompletten 9-GB-Commit
des Basis-Images (bei Task 12/38 zweimal).

**Fix (2026-09-21, deployed):** `_is_pycache_path()` (`.pyc`-Ende ODER
`__pycache__` als Pfad-Komponente) filtert die Pyc-Einträge; `C`-Einträge,
die nur Vorfahren von Pycache-Pfaden sind (reine Vorfahren-Kette), werden
als Rauschen ignoriert. Jede echte Datei-Änderung (`A`/`D`/`C` auf Datei,
z. B. von pip/apt) bleibt immer erhalten → Commit wie gehabt.
Verifiziert: Filter-Simulation gegen echtes `docker diff` (Import +
Download) = 0 verbliebene Einträge; neuer Task-12/38-Build = Alias des
Basis-Images (kein Commit, kein zusätzlicher Speicher).

## System-Skripte mit fester Zugriffs-Klasse

**Ziel:** Die System-Skripte der Workspace-Task regeln die Systematik
und dürfen nicht pro Datei umgestellt werden. Alle sechs stehen
**IMMER in der Wurzel** (weiche Punkt-Konvention: 👤-Skripte beginnen
mit ".", 🔒-Skripte nie):
- 🔒 read-only (Student lesbar, nicht überschreibbar):
  `run.sh` (▶ Ausführen), `init.sh` (Init-Phase 1), `test.sh`
  (🧪 öffentliche Tests)
- 👤 versteckt (Student sieht nie, nicht im Download-Paket):
  `.init_hidden.sh` (Init-Phase 2), `.test_private.sh` (Judge —
  **fix in der Wurzel**), `.test_solution.sh` (🧪 Musterlösung-Testlauf)

**Umgesetzt (2026-09-21, deployed; Namen s. nächste Sektion):**
- `system_file_access(path)` in `services/workspace_service.py`
  (`SYSTEM_FILE_ACCESS`-Map, exakt 6 Pfade) — einzige Quelle der
  Wahrheit, `static/js/workspace.js` spiegelt sie
  (`SYSTEM_FILE_ACCESS` + `systemAccessOf`) für das Kontextmenü.
- Enforcement an allen Stellen:
  - `save_task_file`: fixe Klasse wird IMMER erzwungen (übergebene
    `access`-Werte ignoriert → selbstheilend bei älteren DB-Zeilen);
    🔒-System-Datei in verstecktem Eltern-Ordner → ValueError
    (muss für Studenten lesbar bleiben).
  - `set_file_access`: System-Dateien → ValueError (Tutor-API mappt
    das auf HTTP 400).
  - `set_folder_access`: Ordner darf ein 🔒-System-Skript nicht per
    Erbung verstecken (wäre read-only → hidden).
  - `move_task_file`: alle sechs Skripte sind nicht verschiebbar/
    umbenennbar (Fixed-Paths-Check).
  - Tutor-Kontextmenü: System-Dateien zeigen nur „🔐 Zugriff: …
    (System-Skript — festgelegt)“ ohne Auswahl-Items.
- Studentenseite: hidden → nicht gelistet / 404 bei Read, 🔒 →
  read-only. `sync_assets`/`build_package` filtern 👤 bereits
  (die drei 👤-Skripte landen nie im Student-Docker-Paket).
- Seiteneffekt: Access-Map ist Teil des init-Hashes → Class-Wechsel
  eines System-Skripts triggert automatisch einen Rebuild.

## System-Skript-Renamen + Stubs + Musterlösung-Testlauf

**Ziel (User-Runde 2026-09-21):**
1. **Renamen** auf konsistente Namen (weiche Punkt-Konvention, keine
   harte Enforce-Regel): `init_private.sh` → `.init_hidden.sh`,
   `tests/test_public.sh` → `test.sh` (Wurzel), `test_private.sh`
   (früher Pfad frei) → `.test_private.sh` (**fix in der Wurzel** —
   „flachster gewinnt“ ist damit obsolet), NEU `.run_solution.sh`
   (Tutor-Testlauf). `init.sh`/`run.sh` behalten ihre Namen.
2. **Stubs:** `ensure_system_stubs` legt bei leerem Datei-Baum beim
   Task-Save (Selbstheilung in `on_task_saved`) alle sechs Skripte mit
   deutschem Header-Kommentar + den `.solution/`-Referenzordner (👤)
   an — der Tutor muss die Skript-Namen nicht auswendig wissen.
3. **🧪 „Musterlösung testen“-Button** (Tutor-Workspace): ephemerer
   Workspace `ws-{course}-{task}-0` (student_id=0 reserviert,
   kollidiert mit keinen echten User-/Submission-IDs), frisches
   Volume, alle Dateien inkl. 👤; `.run_solution.sh` overlaid
   `.solution/` über die Wurzel und läuft `run.sh` → `test.sh` →
   `.test_private.sh` durch (Exit-Code des ersten Fehlers zählt —
   die Musterlösung wird auch gegen die privaten Tests geprüft).
   Live-Log per Polling (2,5 s), danach Workspace deterministisch
   gelöscht (erster finaler Status-Poll; Fehlanzeige räumt der
   Idle-Cull auf).

**Umgesetzt (2026-09-21, deployed):**
- Backend: `SYSTEM_FILE_ACCESS` (6 Pfade), `SYSTEM_STUBS`,
  `ensure_system_stubs`, `test_run_key`, `judge_script_path` (fixer
  Wurzelpfad statt Name-Suche), `test_run_starter_files` /
  `_hidden_injection_files`, `move_task_file` blockt alle sechs.
- Tutor-API: `POST /workspace/test-run`, `GET /workspace/test-runs/{id}`
  , `POST /workspace/test-runs/{id}/stop`.
- Agent: durchgängig `.init_hidden.sh`; Legacy-Orphan
  `.private/init_private.sh` wird beim Build automatisch entfernt.
- Template: 🧪-Button + Terminal-Panel (Tutor); der Student-Button
  „🧪 Test“ läuft `test.sh`.
- Bugfix (compose_gen): der init-private-Service hat den Mount
  `./.init_hidden.sh:/.init_hidden.sh:ro` + `command: bash
  /.init_hidden.sh` — vorher lag das Skript top-level im Paket, wurde
  aber nie gemountet (stummer No-Op).
- Task 37 migriert (Disk-Rename + DB-Pfade; `init.sh` schreibt nicht
  mehr nach `.tests/` — Phase 1 darf 👤 nicht berühren; alle bla*-Files
  kommen aus `.init_hidden.sh`); Agent-Artefakt-Stores
  (`.private`/`.seeds`/Manifest) einmal manuell geleert — sie hielten
  Stale-Artefakte aus der Zeit vor den Zugriffsklassen (Geist-Dateien
  im [init]-Baum, 👤-Seed in frischen Student-Volumes).
- LLM-Workspace-Prompt + `create_workspace_example.py` auf die sechs
  Namen umgestellt.

**Konventionen:**
- `.solution/` (👤) spiegelt die editierbaren Pfade 1:1 —
  `.run_solution.sh` kopiert `.solution/.` über die Wurzel und
  führt dann die drei Lauf-Skripte aus (Standaard-Stub).
- `.init_hidden.sh` (Phase 2) sollte NUR in 👤-Pfade schreiben:
  Schreib-Ergebnisse in ungemountete Bereiche (z. B. ✏️-Wurzel)
  landen im Task-Image, das vom Student-Volume überschattet wird →
  unsichtbar/verloren.

## Artefakt-Sammlung entfernt (2026-09-21)

Die Ergebnis-Datei-Sammlung (`workspace_artifacts`-Globs → Agent-
`collect_artifacts` → Studenten-Galerie + Grading-Prompt-Sektion) ist
**restlos entfernt**. Begründung: Bilder/Dateien sind bereits im
Workspace-Dateibaum als Preview ansehbar; 100-kB-Truncation war unelegant.

- **Ergebnisdaten fürs Grading kommen per Test-Output:** test.sh /
  .test_private.sh lesen relevante Metriken aus Programm-Output/Logging-
  Dateien aus und `echo`en sie KNAZ (im Workspace-Generations-Prompt
  verankert). Der Grading-Prompt verlor die `__ARTIFACTS__`-Sektion;
  Judge sieht nur Student-Dateien + Test-Output.
- Entfernt: DB-Spalte (`column_drops` „tasks“), Modell-/API-Felder,
  Spec-Key (Backend + Agent `ALLOWED_KEYS`), `collect_artifacts` +
  `b64` (Agent), `RunJob.artifacts`, `MAX_ARTIFACT_*`, Studenten-
  Galerie (HTML + JS), Tutor-Input-Feld, Prompt-Bullets, Fingerprint-
  Teil (compose_gen → Pakete werden einmal neu signiert).
- INIT-ARTEFAKTE (init.sh/.init_hidden.sh, Manifest, [init]-Einträge)
  sind ein anderes Feature und UNANGETASTET.

## Tree-Reihenfolge: Dateien + Ordner + [init] (Drag & Drop, 2026-09-21)

Alle Einträge (Dateien, Ordner, 📦 [init]-Artefakte) haben IMMER einen
Drag-Handle. Drop im **selben Ordner** = neu ordnen (rein Anzeige, ohne
Move-Gates); Drop in einen **anderen Ordner** = Move (Gates wie bisher).
Struktur bleibt unverändert — nur die Sicht/Anordnung ändert sich.

- **Tutor = global**: `POST /tasks/{id}/workspace/reorder {order: [Pfade]}`
  (eine Gruppe, gleicher Parent). Routing: Datei →
  `task_workspace_files.sort_order`; Ordner + [init]-Artefakte → neue
  Tabelle `task_workspace_orders` (task_id, path, sort_order;
  reine Anzeige — wirkt weder auf Disk/Sync noch Grading; auch für
  Ordner OHNE Zugriffs-Klasse, die keine `TaskWorkspaceFolder`-Zeile
  haben). Alter `/files/reorder`-Endpoint entfernt. [init]-Einträge sind
  damit sortierbar (früher fest); unbekannter Pfad → 400. Ordner-Moves
  ziehen die `task_workspace_orders`-Zeilen mit (`move_folder`).
- **Student = nur lokal**: `WorkspaceUI.init({localOrderKey})`
  (`"wsorder-<user_id>-<task_id>"`); Reorder schreibt nur in
  localStorage (`{[dir]: {dirs: [Namen], files: [Namen]}}`) + re-rendert
  — KEIN API-Call. Neue/umbenannte Einträge sortieren ans Gruppen-Ende.
  Die Student-List-API annotiert die (Agenten-)Dateien mit der
  Tutor-`sort_order` aus der DB + liefert `folder_order` → die Tutor-
  Reihenfolge ist Default; die lokale Student-Ordnung hat Vorrang.
- List-API (Tutor + Student): `folder_order`-Map (path → sort_order für
  Ordner + [init]-Artefakte); [init]-Einträge bekommen `sort_order` aus
  `task_workspace_orders` (null = Ende der Datei-Gruppe).
- JS: Handle-Zugriff für Ordner nicht mehr an `canDragDir` gekoppelt
  (alle Ordner ziehbar; Moves blockt der Drop-Gate mit Fehlermeldung).
  Drop-Matrix: Datei→Datei (gleicher Ordner) = Reorder (📦 erlaubt,
  „read-only“-Toast entfernt); Ordner→Ordner-Zeile (gleicher Parent) =
  Reorder (halbe Zeile = davor/danach); Ordner→Datei-Zeile (selber
  Ordner) = Ordner ans Ende der Ordner-Gruppe; sonst = Move.
  Leere client-seitige Ordner (extraDirs) fehlen im Tutor-Payload
  (keine Backend-Präsenz → Grund-Position).
- Beiliegend: Student-Banner ohne Datei-Anzahl („· X Datei(en) im
  Dataset-Bereich“ — falsche Zählung, entfernt).

## Grading mit Test-Ergebnissen + optionale System-Skripte (2026-09-23)

- **Grading mit public + private Tests:** `_grade_workspace` führt je
  vorhandenem Skript einen eigenen Step aus: `bash test.sh` (falls 🔒
  hinterlegt) + `bash .test_private.sh` (Judge, falls 👤 hinterlegt) —
  jeder mit `── <Name> ──`-Header; beide Outputs fließen als
  TEST-ERGEBNISSE ans Korrektor-LLM (Prompt-Bullet angepasst).
  WorkspaceRun-Zeile: `command` = Steps gekettet, stdout/stderr kombiniert
  (je Step Sequenz), Timeout = OR über Steps. Beide Skripte fehlend →
  saubere Fallback-Meldung, Grading läuft ohne Test-Ausgaben.
- **Download-Paket: ro-Standardverhalten** — jede 🔒-Datei (run.sh,
  test.sh, init.sh UND beliebige weitere wie tensorboard.sh) wird
  standardmäßig ins Paket gelegt, nicht nur die 3 System-Namen.
  Live-Paket („eigene Lösung“): Snapshot liest tar DURCH die ro-Mounts,
  wird danach um die Task-🔒-Dateien ergänzt — die ✏️-Dateien bleiben die
  STUDENTEN-Version (Bugfix: wurden zuvor durch die Task-Version
  überschrieben).
- **Alle 6 System-Skripte optional** (crash-frei, verifiziert):
  - `run.sh` fehlt → kein Start-Button (Play-Button ist generisch)
  - `test.sh`/`.test_private.sh` fehlen → Grading ohne Test-Ausgaben
  - `init.sh`/`.init_hidden.sh` fehlen → kein Task-Image, Basis-Image
    direkt (Agent: unauflösbares task_image + init.sh fehlt → Spec-Image;
    init.sh vorhanden + fehlendes Image → harter 409)
  - `.test_solution.sh` fehlt → saubere 400 („Kein .test_solution.sh
    hinterlegt“)
  - Stubs (`ensure_system_stubs`) bleiben: nur beim LEEREN Datei-Baum
    (Selbstheilung); LLM-generierte Aufgaben liefern die Skripte selbst.
  - `workspace_task_prompt.py`: LLM erklärt, dass alle Skripte optional
    sind (Folge je fehlendem Skript) + Regel-Sektion konditional.

## Rename `.run_solution.sh` → `.test_solution.sh` + robuster Stub (2026-09-24)

**Ziel (User-Runde 2026-09-24):** Naming-Konsistenz mit `test.sh` /
`.test_private.sh` + klarer Bezug zum „🧪 Musterlösung testen“-Button.
Zusätzlich (Option A): der Stub wird ROBUST gemacht, damit der Testlauf
funktioniert, wenn `run.sh`/`test.sh`/`.test_private.sh` (oder mehrere)
fehlen — und die Sequenz vom LLM/Tutor frei anpassbar bleibt
(z. B. mehrere Run-Skripte). Fehlender `.solution/`-Ordner → Ausgabe
„Keine Lösung spezifiziert“ (Exit 0, kein Crash).

**Umgesetzt (dev-deployed; vom User zu testen):**
- Rename an allen Stellen: `TEST_SOLUTION_SCRIPT` (Konstante),
  `SYSTEM_FILE_ACCESS` + `SYSTEM_STUBS`, `workspace_test_run` (400-Check
  + Command), JS-Spiegel (`SYSTEM_FILE_ACCESS` + moveGate-
  `systemScripts`), Template (Legende + Button-Title),
  `workspace_task_prompt.py` (Modell-Liste + Konvention +
  Optional-Liste), `create_workspace_example.py`, models.py-Docstring.
- **Neuer robuster Stub** (replaces `set -e`-Version, die bei fehlender
  `run.sh` crashete und als letzte Zeile `[ -f x ] && …` bei fehlender
  Datei Exit 1 lieferte): jede Sequenz `── <Name> ──`-Header, fehlende
  Skripte werden übersprungen, Exit-Code des ERSTEN Fehlers zählt
  (spätere Steps dann skipped); fehlender `.solution/` → „Keine Lösung
  spezifiziert“ + Exit 0. Reihenfolge/Selektion frei anpassbar.
- **Migration** `scripts/migrate_rename_test_solution.py` (--dry-run),
  2 Teile, beide idempotent:
  1. Disk-Datei + DB-Zeile je Task umbenannt.
  2. Stub-Content: bekannte Standard-Varianten (set -e-Versionen,
     vorheriger robuster Stub) → aktueller robuster Stub; INDIVIDUELLE
     Skripte (ohne Standard-Marker) bleiben unangetastet + gemeldet.
  (Erste Fassung der Migration nur Teil 1 — der User hat danach die
  Content-Normalisierung gewünscht, da er beim Testen auf die fragilen
  alten Inhalte gestoßen ist.)
  - AUFFALLUNG: Task 43 `.test_solution.sh` enthält versehentlich eine
    HTML-Seite (Login-Page) statt ein Skript — bewusst NICHT
    überschrieben (kein Standard-Marker), manuell reparieren.
- Nebenwirkung: die Access-Map ist Teil des init-Hashes → Tasks mit
  `init.sh` bekommen nach dem Rename ein neues Task-Image gebaut
  (einmalig; init.sh sieht die 👤-Datei nicht, Ergebnis identisch).

## Testlauf/Grading: frisches Volume garantieren (2026-09-24)

**Bug:** „Musterlösung testen“ (und Grading/Rerun) liefen stumm mit einem
ALTEM Volume, wenn das Cleanup des vorherigen Laufs auf dem Agenten
fehlgeschlagen war: `remove_workspace` schluckt alle Docker-Fehler
(`check=False` auf `rm -f` + `volume rm`) und `workspace_create` schreibt
Starter-Dateien nur bei `fresh=True`. Symptom: `.solution/`-Dateien fehlten
im Testlauf-Container bei ✏️/👤 („Keine Lösung spezifiziert“), bei 🔒
funktionierte es trotzdem (Dateien kommen per ro-Bind-Mount, nicht aus
 dem Volume).

**Fix (Backend):** `ensure_fresh_workspace()` in `workspace_service.py` —
Einweg-Workspaces (Testlauf, Grading, Rerun) anlegen und das `fresh`-Flag
prüfen; bei `fresh=False` mit erwarteten Starter-Dateien: einmal löschen +
neu anlegen, dann klares 502 statt stummer Alt-Daten. Verwendet in
`workspace_test_run`, `_grade_workspace`, `_run_rerun_background`.

**Offen (Agent-Neubuild nötig):**
- `remove_workspace` robust machen (Volume-Rm mit Retry), damit Stale-
  Zustände seltener werden.
- Hähnchen-Ei-Problem: Agent-`task_has_init` prüft das Asset-Dir
  (`init.sh` ODER `.private/.init_hidden.sh`); Tasks mit NUR
  `.init_hidden.sh` (kein `init.sh`) bekommen den ERSTEN Init-Build nie
  getriggert (`.private/.init_hidden.sh` entsteht erst nach einem
  erfolgreichen Build). Aktuell harmlos (Task 44 = No-Op-Stub +
  Basis-Image-Fallback in `_desired_image`), potenziert aber bei realen
  `.init_hidden.sh`-Downloads. Idee: `start_init_build` behandelt
  `init_private_b64` im Request als has-init-Signal.

## Bugfix: Leere Ordner in Volumes materialisieren (2026-09-24)

**Bug:** `.solution/` gelöscht + neu angelegt (leerer Ordner) →
„Musterlösung testen“ lieferte bei ✏️/👤 „Keine Lösung spezifiziert“, bei
🔒 funktionierte es. Ursache: `write_starter_files` schreibt nur
DATEIEN (Temp-Tree + `docker cp`) — ein Ordner ohne Dateien erzeugt kein
Verzeichnis im Volume, `[ -d .solution ]` in `.test_solution.sh` schlägt
 fehl. 🔒-Ordner zeigen das nicht: ro-Bind-Mount, dessen Mount-Point das
Docker-Runtime beim Container-Start anlegt.

**Fix (wiederverwendet die bestehende `folders`-Konvention aus
assets_sync/init_build):**
- Agent `write_starter_files(key, files, folders=None)`: Ordner vor der
  `docker cp` in den Temp-Tree legen — docker cp ist tar-basiert und
  behält leere Verzeichnisse bei (empirisch verifiziert).
- Agent `workspace_create`: akzeptiert `folders` (wird bei frischem
  Volume wie die Starter-Dateien geschrieben).
- Client `create_workspace(..., folders=...)`.
- Backend: neu `materialize_folders(session, task, include_hidden=False)`
  — explizite Ordner mit effektiver Klasse ✏️ (+ 👤 für Einweg-Workspaces);
  🔒 entfällt (ro-Mount). Mkdir ist idempotent — Ordner mit Dateien werden
  dadurch nur redundant angelegt.
- Call-Sites: `ensure_workspace` (Student-Volume, nur ✏️ — 👤 ist für
  Studenten unsichtbar), Testlauf/Grading/Rerun (✏️+👤, über
  `ensure_fresh_workspace(..., folders)`; der Freshness-Guard greift jetzt
  auch, wenn nur Ordner erwartet werden).

**Einschränkung:** Bestehende Student-Volumes holen neue leere Ordner erst
beim nächsten Reset (Semantik frischer Volumes — wie bei ✏️-Dateien).

**Ordner-Persistenz (Tutor):** Damit „gelöscht + neu angelegt“ überhaupt
reproduzierbar ist (und leere `.solution/` nach Reload sichtbar bleibt),
wurde Ordner-Anlage/-Löschung im Tutor auf die gleiche on-disk-Semantik
gestellt wie beim Student:
- Semantik-Wechsel: Eine `TaskWorkspaceFolder`-Zeile = „expliziter
  Ordner“ — `access=NULL` bedeutet jetzt explizit „edit“ (Zeile bleibt
  bestehen; früher: NULL = Zeile löschen). Die Spalte wurde nullable
  migriert (`scripts/migrate_folder_nullable_access.py`, einmalig,
  inkl. defensivem Dedup doppelter (task_id, path)-Zeilen).
- Service: neu `create_task_folder` (Disk + Zeile NULL) und
  `delete_task_folder` (Subtree: Disk `rmtree` + alle Datei- und
  Ordner-Zeilen, Python-Präfix-Filter statt SQL LIKE); `set_folder_access`
  mit NULL behält/legt die Zeile an (inkl. Self-Healing-Dedup vor dem
  Write).
- Tutor-Routes: `POST /tasks/{id}/workspace/folders` +
  `DELETE /tasks/{id}/workspace/folders/{path:path}` (Subtree-Semantik,
  Init-Artefakt-Subtrees → 403); Student-Routes existierten bereits.
- JS: `deleteDir` nutzt bei `folderApi=true` immer den einen DELETE-Call
  (vorher nur bei impliziten on-disk-Ordner-Einträgen); Tutor-Template
  bekommt `folderApi: true` (war nur im Student-Template).

## Rename `init.sh` → `.init.sh` + Hidden-Klasse + b64-Over-the-Request (2026-09-24)

**Ziel:** `init.sh` war 🔒 read-only im Student-Workspace sichtbar —
Studenten konnten es versehentlich selbst ausführen (obwohl es bereits
per Init-Build gelaufen war) und wurden verwirrt. Es wird jetzt 👤
versteckt und in `.init.sh` umbenannt (weiche Punkt-Konvention für
👤-Skripte, wie `.init_hidden.sh`/`.test_private.sh`/`.test_solution.sh`).

**Funktionell ändert sich nichts am Init-Build:**
- Phase 1 läuft wie bisher (darf in ✏️+🔒 schreiben, NIE in 👤).
- `.init.sh` fehlt jetzt aber im Asset-Sync (hidden-Filter) → der Agent
  holt das Skript per **Init-Request-Body** (`init_b64`, analog
  `init_private_b64`) und persistiert es in der privaten Region
  `.private/.init.sh`. Phase 1 kopiert es von dort nach `tmp/.init.sh`
  und läuft es als `bash /workspace/.init.sh`.
- **Paket-Ausnahme:** `.init.sh` steckt IMMER im Download-Paket
  (`workspace/.init.sh`, alle Varianten: Student/Lösung/Tutor) — der
  lokale `init`-Service braucht es. Es ist das einzige 👤-Skript, das
  im Studenten-Paket landet (Musterlösung/private Tests tun das nicht).

**Umgesetzte Stellen:**
- Backend `workspace_service.py`: `INIT_SCRIPT = ".init.sh"`,
  `SYSTEM_FILE_ACCESS[".init.sh"] = "hidden"`, `SYSTEM_STUBS`-Eintrag
  (+ Hinweis „Für Studenten versteckt (👤) — sie sehen nur das
  Ergebnis“); `init_build` liest `init_b64` von der Task-Disk und reicht
  es an den Client; `task_has_init`/`task_init_hash` via Konstante
  (Hash wechselt für alle Tasks: Access-Map-Pfad + Klasse).
- `compute_client.py`: `init_build(..., init_b64=None, ...)`.
- Agent `docker_ops.py`: neuer `_persist_init_scripts(course, task,
  init_b64, init_private_b64)` (schreibt/entfernt `.private/.init.sh` +
  `.private/.init_hidden.sh`; räumt Legacy `init.sh`/`init_private.sh`
  auf) — läuft in `start_init_build` als ERSTER Schritt VOR dem
  `task_has_init`-Check (fixt den latenten Race: Task mit nur
  `.init_hidden.sh` baute nie, weil die Quelle erst im Build-Thread
  persistiert wurde). `task_has_init` prüft jetzt NUR noch `.private/`.
  Persistierung aus `_do_init_build_core` entfernt (Before-Snapshots
  laufen danach → Rollback räumt die Skript-Quellen nicht auf).
  Seed-Filter + Manifest-Private-Liste schliegen beide Skript-Quellen
  aus (keine [init]-Geister-Einträge).
- Agent `main.py`: `init_build`-Route parst `init_b64`.
- `compose_gen.py`: `build_package` kopiert `.init.sh` aus dem
  hidden-Dict explizit nach `workspace/.init.sh` (steckt in KEINEM
  Snapshot — hidden fehlt im Container); `has_init`-Check + Compose
  `command: bash /workspace/.init.sh` + README-Tabellenzeile/Texte.
  Tutor-Paket: generischer hidden-Loop legt zusätzlich `./.init.sh`
  top-level ab (hid_mounts-Quell-Existenz für init-private/verify).
- Prompt `workspace_task_prompt.py`: `.init.sh (👤)` in der Modell-Liste
  + Feld-Beschreibung in die "hidden"-Sektion verlegt (war in
  "readonly") + alle Referenzen (Dataset-Katalog, Image-Umgebung,
  Optional-Liste, folders-Hinweis).
- JS `workspace.js`: `SYSTEM_FILE_ACCESS[".init.sh"] = "hidden"` (Tutor:
  Kontextmenü zeigt „🔐 Zugriff: 👤 versteckt (System-Skript —
  festgelegt)“) + .init.sh-Ergebnis-Labels (📦-Marker).
- Tutor-Template: Skript-Konventionen-Legende, 📦-Legende, moveGate-
  systemScripts-Liste (`.init.sh` in Wurzel fixiert).

**Migration** (`scripts/migrate_init_sh_hidden.py`, einmalig,
`--dry-run`, idempotent, Konflikt-Check): DB-Zeile `init.sh` →
`.init.sh` (Disk-Rename + raw DB-Update `path`/`access="hidden"` —
NICHT via move_task_file/set_file_access, die System-Skript-Guards
blockieren) + Orphan-Fall (Disk ohne Zeile) via save_task_file. Danach
pro betroffener Task `on_task_saved` (Asset-Sync purgt das alte
`init.sh` vom Agenten + Init-Build mit neuem Hash). 6 Tasks migriert
(37, 38, 41, 43, 45, 46); alle Builds → `ready` (alias auf
Basis-Image, da keine Image-Änderungen).

**Deploy-Reihenfolge (Pitfall):** Code → compute-agent REBUILD →
Migration. Umgekehrt/unterbrochen: alter Agent sieht `base/init.sh`
nicht mehr als Keep-Pfad → Purge → `task_has_init` false; und
Backend `task_has_init` (`.init.sh`) ist vor der Migration false →
Student-Container fällt still auf das Basis-Image zurück.

## Betriebs-Notizen

- init_hash-Format ist an Backend UND Agent-Ref gekoppelt — bei Änderung
  an ALLEN Stellen synchron ändern.
- `.init.sh`/`.init_hidden.sh`-Quellen leben NUR in `.private/` (per
  Init-Request b64 persistiert) — niemals im Asset-Sync (hidden-Filter).
- Status-Strings der UI bleiben: `none|ready|building|failed|idle`
  (+`skipped`-Chip).
- Bind-Mount-Quellen = Host-Pfade (`_daemon_path`).
- Volume-Seed nur bei NEUEN leeren Volumes (Student-Reset nötig).
