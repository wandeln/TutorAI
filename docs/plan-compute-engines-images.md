# Plan: Compute-Engines + Image-Specs

Stand: 2026-09-18 · Status: **umgesetzt** (Phasen unten, ✅ = fertig)

## Ziel

Komplexe Coding-Aufgaben (NN-Training, C++, Assembler, Server) laufen auf
Compute-Engines (Docker-Hosts mit Compute-Agent). Die Engine- und
Image-Verwaltung soll logisch, sicher und anwenderfreundlich sein:

```
Admin (global)
└── Engines anlegen/entfernen (z. B. "local" aus dem Compose)
    └── globale Image-Specs verwalten
        └── auf ANY Engine installierbar

Prof (Kurs)
└── Engines des Kurses anlegen/entfernen (eigene Server, eigenes Passwort)
    └── Kurs-Image-Specs verwalten (+ globale Specs)
        └── nur auf KURS-Engines installierbar

Aufgabe
└── wählt: Engine-Pool (Liste, geordnet) + Image-Spec
```

## Kern-Entscheidungen (aus der Design-Diskussion)

1. **GPU je Engine, nicht je Image/Task:** Der Agent meldet seine GPUs
   im Health (`gpus: [0, 1, …]` via nvidia-smi). Die Engine-Registry
   trägt pro Engine die erlaubten GPUs (`gpus`: `null` = alle,
   `"none"`, `[0, 2]`; Admin-UI: Checkboxen). Alle Container der Engine
   starten automatisch mit den erlaubten GPUs. GPU-Kompatibilität von
   Images ist engine-abhängig (CUDA/ROCm/Versionen) → KEIN GPU-Modus in
   der Spec, kein GPU-Routing (`need_gpu` weg → erste gesunde Engine
   des Pools). Escape-Hatch: dieselbe Engine zweimal registrieren
   (gleiche URL/Key, andere Namen/Regeln).
2. **Image-Spec = reines Dockerfile, Installation = konkretes Image auf
   einer Engine.** Name + Dockerfile sind separate DB-Felder; es gibt
   kein Wrapper-YAML, keine `variants`, keine kuratierten `aicampus/*`-
   Images mehr — alle Specs leiten von öffentlichen Bases ab. Das LLM
   schreibt das komplette Dockerfile (Konventionen NUR im Prompt);
   Gates = Validierung (FROM öffentlich, kein COPY/ADD, ≤50 KB) +
   Vollanzeige + Bestätigung vor dem Build.
3. **Deterministische Tags:** `aicampus/spec/{name}:{hash12}`, Hash =
   SHA256 des normalisierten Dockerfiles (Kommentare/Weißraum raus).
   Gleiche Spec auf N Engines → gleiche Tags; "installiert?" =
   `image_exists`. No-op-Specs (nur `FROM`) bauen nichts — das
   Base-Image wird direkt referenziert; die Installation pullt die
   Base immer (explizite Admin-Handlung, nicht an Autopull gekoppelt).
4. **Dataset-Sharing existiert schon:** Assets liegen 1× pro Engine pro
   Aufgabe (`ASSET_ROOT/{course}/{task}`), read-only in allen
   Student-Containern gemountet. Kein zusätzlicher Aufwand.
5. **Engine-Pool pro Aufgabe** (`workspace_engines` als geordnete Liste;
   heute 1 Element). Routing v1: erste gesunde Engine der Liste (lokal
   bevorzugt). Fehlt die Engine → Fehler-Banner, **kein** stilles Fallback.
   Lastverteilung (Routing v2) = später, Health meldet dann auch
   Container-Load.
6. **Downloads:** Student → nur Aufgaben-Template (Studenten-Version).
   Tutor → Musterlösungs-Template (besteht schon). Einreichungs-Snapshot-
   Download für Studenten wird entfernt. Große Datasets (>100 MB) kommen
   nicht ins Paket — stattdessen `fetch_data.sh` (URL + Checksum aus der
   Dataset-Spec).
7. **Sicherheit:** Access-Key je Engine (existiert, HMAC-Tokens).
   Image-Build auf Engine = Code-Ausführung auf dem Host → nur wer die
   Engine/Spec verwalten darf, kann bauen; Build-Anfrage zeigt immer die
   komplette Spec an.

## Image-Spec — reines Dockerfile

Die Spec ist ein komplettes, self-contained Dockerfile (Name = eigenes
DB-Feld; es gibt kein Wrapper-YAML mehr):

```dockerfile
FROM python:3.11-slim     # öffentliches Base — erste Nicht-Kommentar-Zeile
RUN pip install --no-cache-dir numpy==1.26.4 pandas==2.2.2 …
WORKDIR /workspace
```

Validierung (`compute_agent/image_spec.py`): `FROM` mit öffentlichem
Base-Ref (kein localhost-/Registry-Namen mit Port oder Subdomain), kein
`COPY`/`ADD` (Datasets laden die Container selbst von CDNs), ≤50 KB.
Legacy-Detection: `name:`/`base:`-Zeilen → Friendly-Error "altes Format".

Tags: No-op-Spec (nur `FROM`) → das Base-Image selbst (Installation =
Pull); sonst `aicampus/spec/{name}:{hash12}` (Hash = normalisiertes
Dockerfile). Das GPU-Verhalten kommt NICHT aus der Spec, sondern aus
der Engine-Konfiguration (s. Kern-Entscheidung 1).

## Datenmodell

Neue Tabelle:

```
image_specs
├── id            PK
├── scope         VARCHAR   "global" | str(course_id)
├── name          VARCHAR   (scope, name) UNIQUE
├── dockerfile    TEXT      Single Source of Truth (komplettes Dockerfile)
├── created_by    INTEGER
└── created_at / updated_at
```

`tasks` (neue Spalten, Migration):

```
├── workspace_engines   VARCHAR  JSON-Liste Engine-Namen (geordnet, v1: 1)
└── workspace_image     VARCHAR  Image-Spec-Name (Kurs-Scope > global)
```

`workspace_gpu` bleibt als Legacy-Feld (Read-Only, UI ignoriert es); das
GPU-Verhalten kommt aus der Engine-Konfiguration (`gpus`).

## Agent-Endpunkte (neu)

| Endpoint | Beschreibung |
|---|---|
| `GET /images` | Lokale Images + Build-Status/Log-Tail (Label `aicampus.spec.*`) |
| `POST /images` | `{name, dockerfile}` validieren → Tag berechnen → Base-Pull (No-op) oder Background-Build starten |
| `DELETE /images/{ref}` | Löschen (Sperrung: Build läuft / Container nutzt Image) |
| `GET /health` | + `gpus` (Liste der GPU-Indizes via nvidia-smi), + `active_workspaces` |

Auth: neuer Op-Scope `images` (HMAC-Token wie bisher).

Build läuft im Hintergrund (Thread), Status via `GET /images`
(`building` + Log-Tail) — dauert je nach Paketen Minuten, blockiert also
keine HTTP-Requests.

## AICampus-Endpunkte (neu)

| Endpoint | Rolle |
|---|---|
| `GET/POST /api/admin/image-specs` | globale Specs |
| `PUT/DELETE /api/admin/image-specs/{id}` | globale Specs |
| `GET/POST /api/courses/{id}/image-specs` | Kurs-Specs (Prof) |
| `PUT/DELETE /api/courses/{id}/image-specs/{spec_id}` | Kurs-Specs (Prof) |
| `POST …/image-specs/generate` | LLM generiert Spec-YAML (validiert, UI bestätigt) |
| `POST …/image-specs/{id}/install` | `{engine}` → Agent-Build (nur eigene Engines) |
| `DELETE …/image-specs/{id}/install` | Image von Engine entfernen |
| `GET …/compute/engines/{name}/images` | Agent `/images` proxyen |

Task-Save-Seiteneffekt: referenzierte Spec auf **alle** Engines des
`workspace_engines`-Pools sicherstellen (no-op-Check oder Build starten).

## UI

- **Admin-Konsole → Compute:** Engine-Liste mit Health + GPU (detektiert,
  kein Checkbox), Status je Engine inkl. installierter Image-Specs;
  globale Image-Specs (Liste, neu anlegen (manuell/LLM), editieren,
  löschen, installieren auf Engine).
- **Kurs-Settings → Compute:** eigenes Engine-Registrier-Formular (statt
  Raw-JSON-Textarea), Kurs-Specs + globale Specs, Installation nur auf
  Kurs-Engines.
- **Task-Editor (Workspace):** Engine-Dropdown (global + Kurs, mit
  Health-Badge) + Image-Spec-Dropdown (mit Base-Badge) +
  „neue Spec anlegen" (Modal: Dockerfile-Editor + LLM-Button).
  GPU-Checkbox komplett weg (GPU kommt aus der Engine-Konfiguration).

## Phasen & Status

| # | Inhalt | Status |
|---|---|---|
| 1 | Agent: `image_spec.py` (Parse/Hash/Dockerfile), `docker_ops` (list/build/remove, gpu_info), Endpoints `/images*`, Health-Erweiterung, Op `images` | ✅ |
| 2 | Backend: `ImageSpec`-Tabelle + Migration (`tasks.workspace_engines/workspace_image`), Seed-Specs (kuratierte Images), `workspace_service` (Routing v2, Health-GPU, Image-Ref-Injektion in Workspace-Spec) | ✅ |
| 3 | API: Spec-CRUD (global + Kurs), Install/Uninstall, Engine-Image-Liste, LLM-Spec-Generierung, `compute_client`-Methoden, Task-Save-Prebuild | ✅ |
| 4 | UI: Admin (Engines + globale Specs), Kurs-Settings (Engines + Kurs-Specs), Task-Editor (Engine + Spec), GPU-Flags aus den Formularen | ✅ |
| 5 | Downloads: Student nur Template (Snapshot-Download entfernen), `fetch_data.sh` + README für große Datasets, Image-Spec-konformes `images/` | ✅ |
| 6 | LLM-Task-Generierung: Engines + installierte Specs als Kontext, Vorschlag neuer Spec bei fehlendem Match | ✅ |

## Migration / Abwärtskompatibilität

- Bestehende Aufgaben ohne `workspace_image`/`workspace_engines` laufen
  unverändert weiter (Preset-Image via `LOGICAL_IMAGES`-Legacy-Fallback;
  `migrate_image_specs_v2.py` setzt für sie `workspace_image` = Seed-Name).
- `LOGICAL_IMAGES` bleibt im Agent als Legacy-Fallback (wird nicht mehr
  erweitert); neue Tasks nutzen Image-Spec-Namen.
- Agent-Registry-Feld `gpu` (Legacy) wird im Agent via Fallback in
  `gpus` ("all"/"none") übersetzt; JSON-Format bleibt kompatibel.
- Kurs-Specs im alten YAML-Format sind nach der Migration ungültig
  (Validierung: "altes Format") → neu als Dockerfile anlegen.
- Alte Agent-Version ohne `/images`: AICampus erkennt via Fehler und
  meldet „Agent braucht Update" (kein Crash).

## Offene / bewusste Nichts

- **Routing v2 (Lastverteilung):** Health meldet Container-Load mit;
  Pick-Logik wechselt von „erste gesunde" zu „wenigste beladene" — erst
  wenn >1 Engine pro Kurs üblich ist.
- **CUDA/ROCm-Feature-Matrix:** Engines bekommen ein Freitext-Feld
  „Features" (nur Anzeige); harte Abhängigkeitsprüfung bleibt auf GPU.
- **Image-Push zwischen Engines:** bewusst nicht — Installation aus der
  Spec (Public-Base + Pakete) ist der portable Weg.

## ÄNDERHISTORIE / Hinweise

- **2026-09-18 (Dockerfile-Specs + Engine-GPU-Regeln):**
  - **Image-Spec = reines Dockerfile:** `ImageSpec.spec_yaml` →
    `dockerfile` (Migration `migrate_image_specs_v2.py`: RENAME COLUMN,
    DROP `gpu_mode`, die 4 globalen Seed-Specs mit neuen
    Dockerfile-Inhalten überschrieben; Legacy-Tasks mit leerem
    `workspace_image` + `preset:` in der Spec → `workspace_image` =
    Seed-Name via PRESET_TO_SPEC). Keine YAML-Specs, keine `variants`,
    kein `gpu_mode`, keine kuratierten `aicampus/*`-Images mehr;
    `compute_images/` + `scripts/build_compute_images.sh` gelöscht.
    Alle Specs leiten von öffentlichen Bases ab
    (python:3.11-slim, debian:bookworm-slim, …).
  - **Validierung (`compute_agent/image_spec.py`):** erste
    Nicht-Kommentar-Zeile muss `FROM` mit öffentlichem Base-Ref sein,
    `COPY`/`ADD` verboten (self-contained), ≤50 KB. Legacy-Detection:
    `name:`/`base:`-Zeilen → Friendly-Error "altes Format". No-op-Spec
    (nur FROM) = Base-Referenz; deren Installation pullt die Base
    IMMER (explizite Admin-Handlung). Tag: No-op → Base selbst, sonst
    `aicampus/spec/{name}:{hash12}` (Hash = normalisiertes Dockerfile,
    Kommentare/Weißraum raus).
  - **GPU je Engine statt je Image/Task:** Agent meldet `gpus` (Indizes
    via nvidia-smi, gecacht) im Health; Engine-Registry-Entry bekommt
    `gpus` (`null` = Default alle | `"none"` | `[0, 2]`), Admin-UI:
    per-GPU-Checkboxen in der Engine-Card. `spec.py` akzeptiert `gpus`
    im Workspace-Spec (Legacy-Fallback: `gpu`-Bool); `workspace_service`
    injiziert die `gpus` der Engine in die Workspace-Spec
    (`workspace_spec_for_agent`). `create_container` startet mit
    `--gpus all` bzw. `--gpus "device=…"`. `need_gpu`-Routing komplett
    raus → erste gesunde Engine des Pools. Task-GPU-Checkbox +
    `workspace_gpu`-Filter weg (DB-Spalte bleibt Legacy). Escape-Hatch:
    dieselbe Engine zweimal registrieren (gleiche URL/Key, andere
    Namen/Regeln).
  - **LLM schreibt komplette Dockerfiles:** `prompts/image_spec_prompt.py`
    (Regeln: FROM öffentlich, kein COPY/ADD, Layer-Konventionen,
    `WORKDIR /workspace`, kein GPU-Zusatz ohne explizite Anforderung;
    Konventionen NUR im Prompt — kein Server-Nacharbeiten des Outputs).
    `generate_image_spec(description, name, current_dockerfile)`
    validiert via `validate()`. Task-Generierung: `proposed_image_spec`
    = `{name, dockerfile}`; `gpu_enabled`-Parameter aus Prompt +
    Signatur entfernt.
  - **`compose_gen.py`:** Image-Spec-Dockerfile 1:1 ins
    Download-Paket (Student: nur Template, Tutor: + Musterlösung;
    `images/`-Ordner enthält das Dockerfile, kein eigener
    Build-Wrapper; Datasets laden die Container selbst von CDNs).
    Legacy-Tasks (ohne `workspace_image`) → Seed-Dockerfile aus der DB
    (PRESET_TO_SEED_SPEC).
  - **Deploy:** `setup_compute_server.sh` + `deploy/README.md` +
    Compose-Header: Images werden NICHT mehr beim Setup gebaut —
    Installation läuft per Image-Spec in der AICampus-UI je Engine
    („＋ Image installieren“). `config.py`: `LOGICAL_IMAGES` als
    Legacy-Fallback markiert. `workspace_presets.py`: totes
    `base_requirements`-Feld entfernt.

- **2026-09-18 (Admin-UI-Redesign + Feature-Flag raus):**
  - **Feature-Flag `compute_enabled` komplett entfernt:** Workspace-Aufgaben
    sind immer verfügbar (`config.COMPUTE_ENABLED` weg, Resolver gibt
    hart `enabled=True`; DB-Spalten/API-Felder bleiben Legacy). Task-Typ-
    Dropdown zeigt „Workspace“ ohne Guard; bei fehlender/krank Engine
    degradieren Student-/Tutor-Views sauber mit Fehler-Banner.
  - **Admin-Konsole → Compute:** alte Checkbox „Workspace-Aufgaben
    aktivieren“, getrennte Status-Sektion und „💾 Compute-Einstellungen
    speichern“-Button weg. Neue einheitliche Sektion **Globale
    Compute-Engines**: Zeilen mit Status-Dot + `gpu_info`, Auto-Save bei
    Änderung (PUT `/api/admin/settings`), „⟳ Status aktualisieren“,
    „＋ Engine hinzufügen“. Pro Zeile: 🧪 Test, 📦 **Image-Panel**
    (installierte Images der Engine + 🗑 pro Image + „＋ Image
    installieren“ aus NUR globalen Specs — globale Engines bekommen nie
    Kurs-Specs), 🗑 Engine entfernen (Confirm; Images bleiben auf der
    Engine).
  - **Neuer Endpoint** `DELETE /api/admin/compute/engines/{name}/images`
    (`{ref}`) → Agent `images_remove`; Engine-Verbindung bleibt.
  - **Admin-Spec-Liste ohne „Auf Engine installieren“-Button mehr**
    (Installation erfolgt jetzt pro Engine im Engine-Panel;
    `image-specs-ui.js` bekommt `opts.installModal`, Admin `false`).
  - **LLM passt Specs an:** Spec-Modal im Editier-Mode → „🤖 Mit LLM
    anpassen“ + Änderungswunsch (z. B. „Füge noch nvidia-warp hinzu“);
    `generate_image_spec(current_yaml=…)` + Prompt-Zweig „vorhandene
    Spec minimal anpassen, komplette Spec ausgeben“.
  - **Kurs-Settings:** Tri-State „Workspace-Aufgaben im Kurs“ raus
    (Workspace immer an; Engine-Card bleibt). Kurs-Specs-Sektion nutzt
    noch das alte Install-Modal (`installModal: true`) — Engine-Panel-
    Prinzip folgt in der nächsten Iteration.
  - **Install-Liste filtert bereits installierte Specs:** Neuer
    Agent-Endpoint `POST /images/spec-status` (`{specs: [yaml, …]}` →
    je Spec `{refs, installed, building}`; Ref-Auflösung identisch zu
    `install_spec_image`, inkl. `LOGICAL_IMAGES` + GPU-Variante).
    Die Engine-Image-Proxys (Admin + Kurs) liefern jetzt zusätzlich
    `spec_status` (spec_id → Status; bei alten Agents leer = altes
    Verhalten). Admin-Image-Panel blendet installierte Specs aus der
    Installier-Liste aus, laufende Builds erscheinen deaktiviert mit
    „(Build läuft …)“. Install-Toast unterscheidet jetzt „bereits
    installiert“ (No-op, sofort) von „Build gestartet“. Das erklärt
    auch, warum No-op-Specs (z. B. `python-ml` → kuratiertes
    `aicampus/py-ml`) sofort „vollendet“ melden: das Basis-Image existiert
    bereits, es wird nichts gebaut.
  - **.env/Compose-Engine als sichtbare „local“-Zeile:**
    `COMPUTE_AGENT_URL_EXPLICIT` in `config.py` (zählt nur eine explizite
    Env-Deklaration via Compose/`.env`, nicht der Hardcoded-Default);
    `GET /api/admin/settings` liefert `compute_env_default`. Ist die
    gespeicherte Engine-Liste leer, zeigt die Admin-UI die lokale Engine
    als Zeile mit `.env`-Badge (Images sind damit einsehbar/installierbar
    statt „wird der .env-Default verwendet“). Empty-State heißt jetzt
    „Noch keine Engines vorhanden“. Env-Zeile nicht löschbar (Warning-
    Toast) — sie wird durch jegliche gespeicherte Engine verdrängt
    (Resolver-Logik: gespeicherte Liste hat Vorrang vor dem Fallback).
  - **`<details>`-Redesign der Engine-Zeilen (final):** Jede Engine ist
    jetzt eine zusammenklappbare Zeile (Design analog Kurs-Material-
    Import): collapsed = Status-Dot + editierbarer Name (Auto-Save) +
    🧪 Test + animiertes Chevron; aufgeklappt = URL + HMAC-Key
    (Auto-Save), Image-Liste (lazy load beim ersten Aufklappen,
    ⟳ Refresh, 🗑 pro Image), Spec-Select (nur globale Specs,
    installierte ausgeblendet) + „＋ Image installieren“, unten rot
    „🗑 Engine entfernen…” (Löschung nur dort, mit Confirm;
    Env-Zeile → Warning-Toast). Offene Zeilen behalten ihren
    Zustand über Re-Renders (per Name aus dem Live-DOM gelesen;
    Toggle-Events beim innerHTML-Tausch feuern unzuverlässig),
    neue Engines klappen direkt auf. Alt-Muster `imagesOpenIdx` /
    `toggleEngineImages` / `buildEngineImagesPanel` entfernt,
    Installation ausgelagert nach `installEngineImage()`; Inputs/
    Buttons im `<summary>` toggeln das Details nicht mehr
    (Click-`preventDefault` auf Interaktives, Fokus bleibt erhalten).
  - **`<details>`-Redesign der Image-Spec-Zeilen (final, `image-specs-ui.js`):**
    Die Spec-Liste (Admin + Kurs, gemeinsames Modul) ist jetzt analog
    zu den Engine-Zeilen: pro Spec eine zusammenklappbare Karte —
    collapsed = **editierbarer Name** (Input in der Summary-Zeile,
    wie bei den Engines; Read-only-Scopes zeigen einen Span) +
    GPU-Badge + Scope-Badge + [📦 installieren, nur Kursseite] +
    animiertes Chevron. Aufgeklappt: YAML-Textarea (plain, kein
    CodeMirror mehr), darunter die **LLM-Box** (Design wie die
    LLM-Boxen bei Skript-Kapiteln: `bg-purple-50 border-purple-300`,
    Titel „🤖 LLM-Draft generieren“/„🤖 Mit LLM anpassen“, Prompt-
    Textarea + fliederfarbener Button „Draft generieren“/„Änderung
    anwenden“), darunter Fehler-Box, dann Button-Zeile: links rot
    „🗑 Spec entfernen…” (mit Confirm; installierte Images bleiben
    auf den Engines) bzw. bei neuen Specs „✕ Entwurf verwerfen",
    rechts „💾 Speichern“ (explizit). Read-only-Scopes (Kursseite:
    globale Specs) zeigen nur die YAML-Ansicht. „＋ Neue Spec“ +
    „🤖 LLM-Draft“ oben rechts sind weg → „＋ Image-spec hinzufügen“
    unter der Liste (öffnet eine transiente, noch nicht persistierte
    Karte, auto-open + Fokus). Spec-Modal komplett entfernt
    (`openSpecModal` & Co.); stattdessen Inline-Editing in der Karte.
    Offene Karten behalten ihren Zustand über Re-Renders (per ID aus
    dem Live-DOM gelesen); transiente Entwürfe inkl. Feldwerten und
    Dirty-Flag werden beim Reload erhalten. Schließen einer Karte mit
    ungespeicherten Änderungen fragt nach Confirm. Install-Modal
    (Kursseite) unverändert.

- **2026-09-17 (Phase 4 + 6):** UI + LLM-Generierung fertig.
  - **Gemeinsames UI-Modul** `static/js/image-specs-ui.js` (Admin + Kurs):
    Spec-Liste, Spec-Modal (YAML + CodeMirror + LLM-Draft), Install-Modal
    (Engine-Quellen + optionaler Kurs-Picker, Image-Liste mit Build-Status
    + Auto-Refresh + Löschen). `readonlyScopes: ['global']` sperrt globale
    Specs auf der Kursseite (nur Admin editierbar).
  - **Task-Editor:** Engine-Dropdown (Health/GPU-Badges aus
    `GET /api/courses/{id}/compute/engines`, neu) + Image-Spec-Dropdown
    (GPU-Badge) + „Neue Image-Spec“-Modal (YAML + LLM-Button, Kurs-Scope).
    GPU-Checkbox komplett raus (Quick-Fields + Regen + AI-Apply) — GPU kommt
    aus der Image-Spec. `saveTask()` sendet `workspace_engines`/`workspace_image`.
  - **Kurs-Settings:** Engine-Formular (Name/URL/Key) statt Raw-JSON-Textarea
    (Tri-State „Global/Standard“ vs. „Kurs-eigene“), GPU-Tri-State raus,
    Specs-Sektion via Shared-Modul (Install nur auf effektiven Kurs-Engines).
  - **Admin-Dashboard:** GPU-Checkbox (global + je Agent-Row) raus, Status
    zeigt `gpu_info`; globale Specs via Shared-Modul, Install auf globalen
    UND Kurs-Engines (optionaler Kurs-Picker).
  - **Phase 6:** Prompt bekommt Engines (Health/GPU) + Specs als Kontext;
    neue LLM-Felder `workspace_image`, `workspace_engines`,
    `proposed_image_spec` ({name, description, spec_yaml}, ersetzt
    `needs_new_image`/`new_image_requirements`). Validator prüft Typen +
    Spec-Schema (geteiltes `image_spec`-Modul), Existenz-Checks in
    `ai_generate_task` (erfundene Namen werden verworfen, nicht abgelehnt).
    UI-Apply: Dropdowns befüllen, `proposed_image_spec` öffnet das
    Spec-Modal zum Speichern (PROF/Admin; Tutors sehen den Fehler).
    `gpu_enabled` im Prompt = „irgendeine gesunde Engine hat GPU“ (Health),
    nicht mehr das Legacy-Setting.
  - **Hinweis:** `workspace_gpu` / `workspace_gpu_enabled` bleiben als
    Legacy-Felder in DB/Settings (nicht mehr in der UI, Routing ignoriert sie).

- **2026-09-17 (Phase 5):** Downloads finalisiert. Zusätzlich:
  - `images/` im Paket wird jetzt aus der **Image-Spec** generiert
    (kuratierte `aicampus/*`-Basen werden auf ihre öffentliche Definition
    aufgelöst); ohne `workspace_image` unverändert (Preset-Context).
  - **Bugfix `image_spec.py` (geteiltes Modul!):** `variants.gpu`
    wird jetzt SPARSE geparst (leer = `{}`) — vorher brach ein
    gefülltes-aber-leeres Dict die Fallback-Logik (`gpu: required`
    ohne Variante baute ein Image **ohne Pakete**). Zusätzlich:
    `_variant` validierte die falsche Ebene (jede Spec mit `variants:`
    → 422) und `variants.gpu` erlaubt jetzt zusätzlich direktes
    `apt`/`pip` (Plan-Schema, das der LLM-Prompt lehrt).
  - **Nebenwirkung:** `content_hash` enthält die Varianten → bereits
    gebaute Spec-Images bekommen nach dem Update ein neues Tag und
    werden **einmalig neu gebaut** (idempotent).
