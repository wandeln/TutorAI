# Plan: Komplexe Coding-Aufgaben mit Container-Workspaces

> Status: **implementiert — teils überholt**
> ⚠️ Der aktuelle Stand der Zugriffsklassen (✏️/🔒/👤 statt Pfad-Zonen),
> des 2-Phasen-Init und der Mount-Layouts steht in
> **`plan-workspace-access-classes.md`** (dort maßgeblich). In diesem
> Dokument sind die ursprünglichen Entscheidungen noch mit Pfad-Zonen
> (`data/`, `tests/`, `.solution/`, `.tests/`), `/assets`-Mounts, der
> `.private/`-Disk-Präfix und der YAML-Workspace-Spec beschrieben —
> das Skript-Modell (2026-09-19) und die Zugriffsklassen (2026-09-20)
> haben diese Details ersetzt.
>
> Ziel: Aufgaben, die in der lokalen Python-Sandbox nicht laufen (NN-Training auf
> Dataset, C++/Assembler-Computergrafik, Server-Aufbau) per Docker-Container auf
> separaten Compute-Servern ausführen — mit nutzerfreundlichem UI.

---

## 1. Problem & Ziel

Die heutige Sandbox (`services/sandbox_runner.py`) führt **einen** Python-Code
mit Unit-Tests lokal aus (Timeout 15 s, CPU-Limit, allowlistete Module). Das deckt
Algo-Aufgaben ab, aber nicht:

| Aufgabetyp | Warum die Sandbox nicht reicht |
|---|---|
| NN-Training auf vorgegebenem Datensatz | lange Laufzeit (Minuten–Stunden), große Dateien, torch/sklearn, evtl. GPU |
| C++ / Assembler (Computergrafik) | andere Sprache, Compiler, ggf. Grafik-Libs, Build-Prozess |
| Assembler-Grundlagen | nasm/ld, Register-Debugging |
| Server aufsetzen | Konfig-Dateien + Skripte, laufende Dienste, Verifikation durch Probing |

**Ziel:** Jeder Student bekommt pro Aufgabe eine **eigene, isolierte
Arbeitsumgebung** (Docker-Container mit eigenem Dateisystem/Volume), die auf
einem separaten Compute-Server liegen kann. TutorAI bleibt die einzige
Schnittstelle für den Studenten — der Compute-Server ist für den Browser
unsichtbar.

---

## 2. Kernentscheidungen (Empfehlungen)

### 2.1 Neuer Aufgabentyp `workspace` + "Preset" je Sprache

Nicht je Sprache einen eigenen TaskType, sondern:

- `TaskType.WORKSPACE = "workspace"` (neben `text`, `code`)
- Feld `Task.workspace_preset` wählt das **Preset**: `python-ml`, `cpp`, `asm`, `server`

Das Preset ist reine **Metadaten-Konfiguration** (Image, Editor-Modus,
Standard-Run-Befehl, ob lange Läufe, GPU-Option). Damit gibt es nur **einen**
Execution-Code-Pfad, eine **einzelne** Workspace-UI und pro Sprache nur ein
Preset-Objekt:

```python
# services/workspace_presets.py (Neu)
PRESETS = {
    "python-ml": {
        "label": "Python (ML / Datenanalyse)",
        "image": "tutorai/py-ml:1",
        "editor_mode": "python",
        "default_run": "python3 main.py",
        "main_file": "main.py",
        "long_running": True,          # UI zeigt Job-Progress statt Sync-Wait
        "gpu_capable": True,
    },
    "cpp": {
        "label": "C++",
        "image": "tutorai/cpp:1",
        "editor_mode": "text/x-c++src",  # CodeMirror clike
        "default_run": "g++ -O2 -std=c++17 -o solution main.cpp && ./solution",
        "main_file": "main.cpp",
    },
    "asm": {
        "label": "Assembler (x86-64)",
        "image": "tutorai/asm:1",
        "editor_mode": "nasm",
        "default_run": "nasm -f elf64 main.asm -o main.o && ld -o solution main.o && ./solution",
        "main_file": "main.asm",
    },
    "server": {
        "label": "Linux-Server / System-Verwaltung",
        "image": "tutorai/server:1",
        "editor_mode": "text/x-sh",     # + nginx/modi je Dateiendung
        "default_run": "",              # Student startet Dienste selbst
        "verify_command": "bash .private/verify.sh",
    },
}
```

Tutoren können Run-Befehl, Timeout, GPU, Artefakte je Aufgabe überschreiben.
Neue Sprachen = neues Preset-Objekt + Dockerfile, keine Code-Änderung.

**Das Preset ist die Wiederverwendungs-Einheit:** Eine ganze Serie von
Aufgaben („CNN auf MNIST“, „Lineare Regression auf Housing-Daten“,
„Mini-Transformator“) nutzt **dasselbe** `python-ml`-Image — nur Dateien,
Run-Befehl, Timeout und Artefakte unterscheiden sich. Wiederverwendung,
Pro-Task-Umgebungen und die Layer-Struktur: s. §2.10.

### 2.2 "Compute Agent" — der einzige Manager, lokal UND remote

Der **schlüssigste Architekturentscheid**: Es gibt keinen getrennten
"LocalDocker"- und "RemoteDocker"-Code in TutorAI. Stattdessen:

```
┌────────────────┐  HTTP über SSH-Tunnel  ┌───────────────────────────┐
│   TutorAI      │ ─────────────────────▶ │  Compute Agent (FastAPI)  │
│  (FastAPI,     │  (Port-Forward,        │  auf Compute-Server,      │
│   Web-UI, DB)  │ ◀───────────────────── │  bindet nur 127.0.0.1,    │
└────────────────┘  signierte Op-Tokens   │  verwaltet Docker         │
                    stdout/stderr/tar      └─────────────┬─────────────┘
         ▲                                                │ docker CLI
         │ Cookie/JWT (unverändert)                       ▼
┌────────┴────────┐                          ┌───────────────────────────┐
│  Browser        │                          │ Container pro (Aufgabe,   │
│  (Student/Tutor)│                          │ Student) + Volume          │
└─────────────────┘                          └───────────────────────────┘
```

(Bei einem lokalen Agenten entfällt der Tunnel — TutorAI spricht den Agenten
Direkt auf `127.0.0.1:8700` an, s. §2.8.)

- **Compute Agent**: kleine FastAPI-App (neuer Modul `compute_agent/` im selben
  Repo), läuft per systemd/Docker auf dem Compute-Server, spricht die
  `docker`-CLI. Endpoints: Workspace erzeugen/initialisieren, Dateien
  lesen/schreiben/listen/löschen, Befehl ausführen (sync + async mit
  Log-Tail), Snapshot (tar) erzeugen, Container löschen.
- **TutorAI**: `services/compute_client.py` (httpx-Client) + `WorkspaceService`
  als Orchestrierung (DB, Limits, Grading).
- **Lokal & Remote sind gleichberechtigt (Hybrid):** Der Agent kann auf
  **jeder** Maschine laufen — auch auf dem TutorAI-Server selbst. Dann ist
  er einfach eine zweite systemd-Unit (`tutorai-compute-agent.service`) auf
  demselben Host, der sich mit dem **gleichen Docker-Daemon** verbindet;
  TutorAI zeigt per Config auf `http://127.0.0.1:8700`, **kein SSH-Tunnel**
  nötig. Die Agent-Registry in `GlobalSettings` mischt lokal + remote frei;
  Routing-Regel s. §2.8.
- **Browser spricht nie direkt mit dem Agenten** — alles geht über TutorAI.
  JWT/Cookie-Auth bleibt unverändert; der Agent ist für Studenten unsichtbar
  und nur im internen Netz erreichbar.

### 2.3 Container-Modell: pro (Aufgabe, Student), ephemeral + persistentes Volume

- **Key**: `ws-{course_id}-{task_id}-{student_id}`
- **Image**: aus der Spec (`image:`; Default aus dem Preset) — logischer
  Name, je Node auf cpu/gpu-Variante aufgelöst (s. §2.9)
- **Read/Write-Modell (fix, nicht erweiterbar):** **Read-only by default** —
  Image-Root, `/env/` (Task-Pakete) und `/assets/` (Datasets) sind alle
  read-only; Bibliotheken müssen also nirgends einzeln „read-only gesetzt“
  werden. Der **einzige** schreibbare Ort ist `/workspace` (Student-Volume,
  `tutorai-ws-{key}`). Zusätzliche `mounts:` aus der Spec sind *immer*
  read-only. Eine generische Permission-DSL (`writable: …`) wird
  **bewusst nicht** unterstützt (s. §11). `/assets/` mit den großen public
  Dateien der Aufgabe (Dataset) wird von allen Studenten geteilt, nicht pro
  Student kopiert (s. §2.7).
- **Limits**: `--cpus`, `--memory`, `--pids-limit` (aus der
  Workspace-Spec, Defaults aus dem Preset; s. §2.11)
- **Netzwerk**: Standard `--network=none`; **opt-in Internet pro Aufgabe**
  (`internet: true` in der Spec → dann `--network=bridge` + egress-Filter:
  DNS/HTTP(S) erlaubt, interne Netzadressen blockiert). Der Tutor schaltet
  es explizit je Aufgabe frei (z. B. für `pip install`/`apt` in der Umgebung).
- **Ephemer**: Container wird bei Bedarf erstellt, nach ~20 min Inaktivität
  vom Agenten gekillt (Reaper-Task). **Der State lebt im Volume**, nicht im
  Container → Neustart kostet nur ~Sekunden.
- **GPU**: `--gpus` nur wenn `Task.workspace_gpu` und global aktiviert.

### 2.4 Private Dateien (Tests/Verifikation) sehen Studenten nie

- Starter-/Template-Dateien der Aufgabe: **public** → liegen im Student-Volume.
- Verifikations-Skript (private Tests, `verify.sh`, Musterlösung): liegt auf
  TutorAI unter `data/workspaces/{task_id}/.private/` und wird **nur bei
  Grading** in einen frischen Container gemountet — nie ins Student-Volume.
  (Gleiche Philosophie wie die heutigen PrivateTests.)

### 2.5 Ausführung & Einreichung

**"Ausführen"** (Student):
- Kurze Läufe (< ~30 s, z. B. cpp/asm): synchron, stdout/stderr + Exit-Code.
- Lange Läufe (python-ml): **Job** mit `run_id`, Frontend pollt
  Status + Log-Tail (letzte ~50 Zeilen) + "Stop"-Button. Container läuft
  weiter, Student kann währenddessen Dateien editieren (Lauf belegt eine
  Momentaufnahme der Dateien zum Startzeitpunkt).
- **Artefakte**: nach dem Lauf werden je Task konfigurierte Pfade/Globs
  (`*.png`, `results.json`, `model.pt`) gesammelt. Bilder werden im UI
  gerendert (Loss-Curves, Grafikscreenshots), andere als Liste/Download.

**"Abgeben"** (Student):
- Agent tar-t das Workspace-Volume → TutorAI speichert Snapshot unter
  `data/submissions/{submission_id}/workspace.tar.gz`, Pfad in neuer
  `Submission`-Spalte.
- Grading (neuer Pfad in `grading_service`, analog `_grade_code`):
  1. frischer Container aus Preset-Image
  2. Student-Snapshot nach `/workspace`, private Verifikation nach `.private/`
  3. Verifikations-Befehl ausführen → der **Agent liefert strukturierte
     Ergebnisse** über die API: Exit-Code, stdout/stderr (truncated ~1 MB),
     Artefakt-Dateien (mit Größenlimits)
  4. LLM bewertet **auf der TutorAI-Seite** — der LLM spricht nie direkt mit
     dem Compute-Server. In den Prompt kommen **nur** die relevanten Teile:
     Aufgabenstellung, Quell-Dateien des Studenten (Textdateien aus dem
     Snapshot), Testausgabe, Artefakte (Inhalt bei Text/JSON, Referenz bei
     Bildern). **Kommt nie zum LLM:** Root-Filesystem, `/env/`-Bibliotheken,
     `/assets/`-Datasets — genau die „unnötigen Daten“.
  5. Punkte + Feedback wie heute.

### 2.6 Verbindung: SSH-Tunnel (TutorAI und Compute-Server in getrennten Netzen)

Kein gemeinsames LAN → die Verbindung ist ein **permanenter SSH
Local-Port-Forward** vom TutorAI-Server zum Compute-Server:

```bash
# Auf dem TutorAI-Server, als systemd-Unit `tutorai-compute-tunnel.service`
# (Restart=always; SSH-Auth nur per Key, kein Password)
autossh -f -M 0 -N \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -i /etc/tutorai/compute_ssh_key \
  -L 127.0.0.1:8700:127.0.0.1:8700 \
  tutorai@compute-server
```

- Der Agent bindet auf dem Compute-Server **nur an 127.0.0.1** → der
  SSH-Tunnel ist der einzige Zugangsweg (key-basiert). Kein offener Port im
  Netz, SSH ist die einzige Tür.
- Abbrüche werden automatisch re-established (systemd `Restart=always` +
  `ServerAliveInterval`); kurze Downtimes absorbiert der Client mit
  Retry-Backoff. Health-Polling (~30 s) zeigt den Status in der
  Admin-Konsole + als Banner in der UI.
- **Mehrere Compute-Server** (Scale-out): ein Tunnel pro Server (lokale
  Ports 8700, 8701, …), Agent-Registry in `GlobalSettings`
  (`[{name, url, key, gpu}]`) → Grundstein für Lastverteilung + GPU-Queue.
- Aufstiegsfall: falls später mehr Services zwischen den Servern laufen
  sollen, wäre WireGuard-VPN die saubere Alternative — die
  HTTP-Schnittstelle des Agenten bliebe unverändert.

Bandbreite/Latenz: Datei-Operationen und Job-Polling sind klein; Snapshots
(tar.gz) gehen einmalig pro Abgabe hin/rein — bei kleinen NN-Aufgaben typ.
< 100 MB, kein Problem über einen Tunnel.

### 2.7 Startup-Zeiten & Kapazität

Container-Startup (typischer Server, NVMe, einmal gebautes & gepulltes Image):

| Schritt | Dauer |
|---|---|
| Image pull | 0 s (einmalig gebaut) |
| `docker create` + `start` | 0.5–2 s (mit GPU: +1–2 s, nvidia-container-toolkit) |
| Starter-Dateien ins Volume | 1–3 s (nur kleine Code-Dateien, s. u.) |
| **Erstzugriff bis „bereit"** | **≈ 2–5 s** (GPU: ≈ 3–7 s) |
| Neustart nach Idle-Kill (Volume bleibt) | ≈ 1–3 s |
| Pro „Ausführen" (docker-exec-Overhead) | < 1 s + Programmlaufzeit |

Zwei wichtige Ergänzungen:

- **`import torch` & Co. gehört nicht zum Container-Start, ist aber
  spürbar:** jede Python-Ausführung zahlt 3–10 s Interpreter + Imports.
  Das ist normal und als Laufzeit im Terminal-Panel sichtbar.
- **Datasets werden NICHT pro Student kopiert.** Public-Dateien > ~5 MB
  (Datasets) werden beim Anlegen/Ändern der Aufgabe **einmal** auf den
  Compute-Server gesynct (`/opt/tutorai/assets/{course_id}/{task_id}/…`) und
  read-only nach `/assets/` gemountet. Das Student-Volume enthält nur die
  editierbaren Dateien → Init bleibt schnell, Disk-Bedarf 1× statt 30–40×,
  und der Submission-Snapshot enthält nur den Studenten-Code (kleiner,
  schnelleres Grading).

**Kapazität für 30–40 gleichzeitige Studenten:**
- 30–40 Container sind für Docker/cgroups auf einer Maschine
  unproblematisch (jeder Container ist „nur" eine cgroup). Limitierend ist
  die Summe der RAM-Limits (Preset-Vorschläge: python-ml 4 GB, cpp 2 GB,
  asm 1 GB, server 1 GB → worst case 40 × 4 GB ≈ 160 GB, praktisch deutlich
  weniger).
- **Die GPU ist der Engpass.** 30–40 parallele kleine NN-Trainings teilen
  sich die GPU(s) → konfigurierbares Limit `compute_gpu_max_jobs`
  (Default ~8; kleine CNNs auf MNIST brauchen je nur 1–2 GB VRAM, auf einer
  A40/A100 laufen damit locker zweistellige Parallelität — die GPU soll
  bewusst **nicht** voll ausgelastet werden) + FIFO-Queue mit UI-Status
  „⏳ wartet auf GPU (n vor dir)". CPU-Trainings sind nicht limitiert.

### 2.8 Agent-Auswahl: Hybrid lokal + remote

Die Registry (`compute_agents`) mischt lokale und remote Agenten:

```json
[
  {"name": "local",     "url": "http://127.0.0.1:8700", "key": "…", "gpu": false},
  {"name": "compute-1", "url": "http://127.0.0.1:8701", "key": "…", "gpu": true}
]
```

(`compute-1` ist über den SSH-Tunnel auf lokalem Port 8701 erreichbar, s. §2.6)

**Routing-Regel v1 (deterministisch, einfach debuggbar):**
1. Aufgabe mit `workspace_gpu=true` → nur gpu-fähige Agenten (Queue pro Agent).
2. Alle anderen Aufgaben → lokaler Agent (wenn aktiv & gesund), sonst
   erster gesunder Agent.

→ „Kleine Aufgaben" (kleine CNNs/MNIST auf CPU, C++, Assembler, Server)
laufen direkt auf dem TutorAI-Server (niedrige Latenz, keine Tunnel-Bandbreite),
GPU-Training wandert automatisch auf den Compute-Server.

**Größenordnung lokaler Agent:** Er teilt sich den Host mit der Web-App →
der lokale Agent hat konservative Default-Limits je Container (z. B. 2 CPU /
4 GB RAM, kein GPU), in der Registry je Agent überschreibbar
(`default_limits`) — schützt TutorAI davor, von Student-Containern
verhungernd zu werden.

**Voraussetzung:** Docker auf dem TutorAI-Server. Die Web-App soll dabei
ebenfalls containerisiert werden — das lokale Hybrid-Setup wird zu einer
Compose-File (s. §2.9).

### 2.9 Deployment-Form: Docker Compose & Image-Verwaltung

**Lokales Hybrid als eine Compose-File** (`deploy/compose.local.yml`):

```yaml
services:
  tutorai:
    build: .                          # Dockerfile im Repo
    volumes: ["tutorai_data:/app/data"]   # DB, Medien, Workspaces persistent
    ports: ["127.0.0.1:8000:8000"]
    env_file: .env
  compute-agent:
    build: ./compute_agent            # eigenes, kleines Image
    ports: ["127.0.0.1:8700:8700"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # Agent verwaltet Host-Container
      - tutorai_assets:/opt/tutorai/assets         # geteilte Datasets (read-only für Studenten)
    environment: ["AGENT_KEY=…"]
```

- `docker compose up` → Web-App + lokaler Agent; die Student-Container
  entstehen **auf dem Host** (der Agent spricht den Docker-Daemon über den
  Socket an).
- Hinweis: docker-socket-Mount ≈ Root-Äquivalent auf dem Host — akzeptabel,
  weil der Agent unser eigener, vertrauter Service ist (Standard-Muster, wie
  es Portainer/Watchtower nutzen). Kein sonstiger Zugriff auf den Socket.
- Lokaler Agent braucht **keine** GPU (Routing: GPU-Aufgaben → Compute-Server)
  → kein nvidia-runtime am Agent-Container nötig.
- Nginx/SSL bleibt auf dem Host (proxyt nach `127.0.0.1:8000`) oder wandert
  optional als dritter Service in die Compose; `deploy.sh` wird entsprechend
  angepasst. Die heutige `tutorai.service`-Unit wird ersetzt.
- **Remote-Compute-Server:** jeder führt Docker (mit nvidia-container-toolkit
  bei GPU) + Agenten (Compose oder systemd) selbst. Je Node werden nur die
  Images gebaut/gezogen, die der Node tatsächlich bedient — GPU-Node zieht
  `py-ml-gpu:1`, CPU-Node `py-ml-cpu:1`/`cpp:1`/`asm:1`/`server:1`. Der
  Agent meldet via `/health` seine Images; die Registry trägt die Fähigkeiten
  (gpu, available images) pro Agenten.

**Basis-Images = öffentliche Standard-Images:** Die Dockerfiles in
`compute_images/` starten `FROM` öffentlichen Basis-Images (Docker Hub:
`python:3.11-slim`, `gcc:13`, `nvidia/cuda:…-devel`) und legen nur eine
dünne Ableger-Schicht drauf (z. B. torch/transformers). Die Ableger-Images
(`tutorai/py-ml-gpu:1` etc.) werden auf den **Compute-Nodes** lokal
gebaut/gecacht; in die **Download-Pakete** kommen sie nicht rein — dort
wird dieselbe dünne Schicht lokal aus öffentlichem Basis-Image + pinned
`requirements.txt` gebaut (s. §7.2), d. h. kein Registry-Bedarf.
Dasselbe `requirements.txt` wird dafür in `compute_images/` gepflegt und
vom Compose-Generator in die Pakete kopiert (eine Quelle, zwei Konsumenten
wie bei der Spec).

**Image-Lebenszyklus: einmalig pro Node, dann gecacht.**
- Ein Docker-Image wird nach dem ersten Pull/Build im Image-Store des Nodes
  **gecacht** — jedes `docker create` ab Cache braucht 0.5–2 s, es gibt
  **keinen** Download pro Student oder pro Aufgabe.
- Pull/Build erfolgt beim **Deployment**, nicht zur Laufzeit:
  1. `scripts/build_compute_images.sh` auf dem Node (build aus den
     `compute_images/`-Dockerfiles im Repo), oder
  2. `docker pull` aus einem Registry (Docker Hub / privates), oder
  3. offline: `docker save` → transfer (z. B. über den SSH-Tunnel) →
     `docker load`.
- Optional zieht der Agent fehlende Images beim Start nach
  (`AGENT_AUTOPULL=true`); bei fehlendem Image zeigt die UI „Image wird
  vorbereitet“ statt stumm zu hängen.
- Image-Update (z. B. neues torch) = neuer Tag (`py-ml-gpu:2`) → einmaliger
  Pull je Node. Bestehende Workspaces laufen weiter auf ihrem alten Image
  (Container referenziert sein Image pin), neue Workspaces nehmen das neue.

### 2.10 Preset-Wiederverwendung & Task-Umgebungen (Layer-Modell)

**Viele Aufgaben teilen ein Image — kein Image pro Aufgabe.**

- Ein Image ist ein **teures, kuratiertes, versioniertes Asset** (Build in
  Minuten, mehrere GB, Security-Relevanz). Eine Aufgabe ist **günstige
  Daten** (Dateien + Konfiguration). Deshalb: Preset = Wiederverwendung.
- **Kein pro-Task-Image-Build** (z. B. „für diese Aufgabe zusätzlich
  `transformers` einbaken“): Build-Latenz je Aufgabe, Image-Bloat
  (N Aufgaben × GB), Versions-Chaos, ungeprüfte Pakete. Das
  `workspace_image`-Override (erweitert) zeigt nur auf kuratierte Images.
- Ein Preset kann **mehrere Image-Varianten pro Node-Typ** abbilden
  (`python-ml` → `py-ml-cpu:1` / `py-ml-gpu:1`) — der Agent wählt die
  Variante nach seiner GPU-Fähigkeit (s. §2.9).

**Layers je Container** (von teuer/kuratiert bis günstig/pro-Student):

| Layer | Inhalt | Granularität | Lebenszyklus |
|---|---|---|---|
| **Image** (Preset) | OS, Compiler/Interpreter, Standard-Pakete, GPU-Support | kuratiert, getaggt | pro Node gecacht, Build beim Deployment |
| **Task-Umgebung** | aufgaben-spezifische Zusatz-Pakete (optional) | pro Aufgabe | 1× je Task-Änderung, **von allen Studenten geteilt** |
| **`/assets`** | Datasets, große public Dateien | pro Aufgabe | 1× Sync je Task-Änderung, read-only |
| **`/workspace`** | editierbare Dateien des Studenten | pro (Aufgabe, Student) | persistentes Volume |

**Task-Umgebung — so füllen wir die Lücke zwischen Image und Aufgabe:**

- Optionales Feld `Task.workspace_packages` (pip-requirements-Format,
  z. B. `transformers\nopencv-python`), vom Tutor je Aufgabe pflegbar.
- Beim Speichern/Ändern der Aufgabe baut der Agent **einmal** ein
  geteiltes Target-Verzeichnis (`/opt/tutorai/taskenvs/{course}/{task}/`,
  `pip install --target …`; Wheel-Cache des Agents bleibt auf dem Host).
- Der Container mountet es **read-only** nach `/env`; der Run-Befehl nutzt
  es automatisch (`PYTHONPATH=/env`, `PYTHONDONTWRITEBYTECODE=1`).
- **Kein Student zahlt Installationskosten** — auch nicht der erste:
  Der Download/Build der Pakete passiert bereits beim Task-Save (Tutor-
  Aktion), nicht beim Studenten-Start.
- Image-Update → Task-Umgebung wird invalidiert → beim nächsten
  Task-Save automatisch neu gebaut.
- Nur Python-Presets bekommen dieses Feature (pip ist schnell &
  kontrollierbar). Für **C++/Assembler/Server** gibt es **kein** pro-Task-
  `apt` (langsam, System-Risiko): Die Images sind ausreichend
  vorinstalliert; ein systematischer Mehrbedarf (z. B. ein C++-Kurs,
  der OpenCV braucht) führt zu **einem kuratierten, versionierten
  Erweiterungs-Image** — ein Ops-Eintagesgeschäft, kein per-Task-Mechanismus.

**Faustregel:** „Per pip installierbar“ → Task-Umgebung. „System-Libs,
Compiler, Treiber, GPU“ → kuratiertes Image (Preset/Flavor).

### 2.11 Workspace-Spec (YAML) — Single Source of Truth

Eine Aufgabe definiert ihre Arbeitsumgebung als **ein YAML-Dokument**
(Spalte `Task.workspace_spec`) — im Geist die „docker-compose der
Aufgabe“ plus TutorAI-Erweiterungen. Dieses Dokument ist die **einzige
Quellwahrheit** für die Umgebung:

```yaml
# Workspace-Spec (Beispiel: python-ml)
preset: python-ml            # Preset → Editor-Modi + Defaults
image: tutorai/py-ml         # logischer Name; Agent löst die
                             # cpu/gpu-Variante je Node (s. §2.9)
working_dir: /workspace
run: python3 train.py        # Studenten-„Ausführen“
verify:                      # privat — nie im Student-Paket
  command: python -m pytest .tests/ -v
limits: { cpu: 2, memory: 4g }
timeout: 900                 # Sekunden (max. 7200)
gpu: false
internet: false
artifacts: [results.json, "*.png"]
packages: [transformers==4.40.0]    # → Task-Umgebung /env (s. §2.10)
environment: {}              # zusätzliche Env-Variablen
mounts:                      # zusätzliche read-only Mounts, nur aus dem
  - { source: data/weights.pth, target: /weights.pth }  # Asset-Bereich
```

**Eine Definition, drei Konsumenten:**
1. **Agent (Server):** interpretiert die Spec und materialisiert je
   (Aufgabe, Student) den Container — mit Plattform-Overrides:
   Student-Volume statt lokaler Bind-Mounts, `/assets` + `/env`, Labels,
   Reaper, Netzwerk-Defaults. Der Agent ist die **Vertrauensgrenze** und
   validiert die Spec **erneut** (s. §9).
2. **Student lokal:** der Compose-Generator rendert aus derselben Spec das
   `docker-compose.yml` für den Download (s. §7.2).
3. **Tutor lokal:** dito, zusätzlich mit `verify`-Service + privaten
   Ordnern (`.solution/`, `.tests/`).

**UI-Konsequenz:** Die Spec ist im Tutor-Formular **immer sichtbar und
direkt editierbar** (CodeMirror, YAML-Modus, Validierung inline). Die
Quick-Fields (Preset, Run, Timeout, GPU, Internet, Pakete, Artefakte)
sind eine **Komfort-Layer**, die dieselben Keys schreiben — manuelle
Abweichungen werden dort als „⚙ abgewichen“ markiert (s. §7.0.1).

**Validierung (erlaubt/blockiert):**
- **Allowlist:** `preset, image, working_dir, command, entrypoint, run,
  verify, limits, timeout, gpu, internet, artifacts, packages,
  environment, mounts` (Mounts nur aus dem Asset-Bereich, read-only).
- **Blocklist:** `privileged, ports, device, cap_add, network_mode: host,
  Host-Pfade, build, restart`, Multi-Service-Definitionen.
- Beliebiges `image` ist erlaubt (Tutor = Vertrauensquelle); nicht
  kuratierte Images werden im UI mit Warn-Badge markiert.

**Datenmodell-Effekt:** Die früheren Einzel-Spalten
(`workspace_run_command`, `workspace_verify_command`, `workspace_timeout`,
`workspace_gpu`, `workspace_internet`, `workspace_artifacts`,
`workspace_packages`, `workspace_image`) fallen **weg** — alles steht in
`workspace_spec`. Denormalisiert bleiben `workspace_preset` und
`workspace_gpu` (schnelles Routing/Filtering ohne YAML-Parse).

---

## 3. Datenmodell (Änderungen)

Migrations-Pattern wie bisher: neue Spalten in `migrate_schema()`
(`database/base.py`), neue Tabellen via `create_all()`.

### `Task` (neue Spalten, alle nullable, nur für `workspace` belegt)

| Spalte | Typ | Zweck |
|---|---|---|
| `workspace_preset` | VARCHAR NULL | Preset-Key (`python-ml`, `cpp`, …) — denorm. aus der Spec, für Routing/Filter |
| `workspace_gpu` | BOOL default 0 | GPU-Passthrough — denorm. aus der Spec, für Routing |
| `workspace_spec` | TEXT NULL | **Workspace-Spec als YAML — Single Source of Truth** (s. §2.11): Image, Run/Verify, Limits, Internet, Pakete, Artefakte, Env, Mounts |
| `workspace_dataset` | TEXT NULL | JSON: Dataset-Spec `{source: catalog\|url\|upload\|transform, name, url, size, checksum, transform_manifest}` (s. §7.1) |
| `workspace_assets_status` | TEXT NULL | JSON je Agent: Asset-Status `pending/downloading/ready/failed` (lazy Sync bei Multi-Agent) |

`TaskType` um `WORKSPACE = "workspace"` erweitern (String-Spalte → kein DDL).

### Neue Tabelle `TaskWorkspaceFile`

| Spalte | Typ | Zweck |
|---|---|---|
| `task_id` | FK tasks.id | |
| `path` | VARCHAR | relativer Pfad im Workspace (z. B. `main.py`, `data/mnist.csv`) |
| `size` / `checksum` | INT / VARCHAR(64) | Metadaten (Inhalt liegt auf Disk) |
| `is_public` | BOOL | **abgeleitet aus der Pfad-Konvention** (s. §7.0.2), materialisiert für schnelles Filtern |
| `is_binary` | BOOL | Dataset vs. Textdatei |

Datei-Inhalte auf Disk (wie Medien `data/media/course_{id}/`):
`data/workspaces/{task_id}/<path>` bzw. `data/workspaces/{task_id}/.private/<path>`.
So passen auch größere Datasets ohne DB-Bloat.

### `Submission` (neue Spalten)

| Spalte | Typ | Zweck |
|---|---|---|
| `workspace_snapshot` | VARCHAR NULL | Pfad zu `workspace.tar.gz` |

### Neue Tabelle `WorkspaceRun` (Lauf-Historie, nützlich für Tutor-Debugging)

`run_id`, `task_id`, `student_id`, `started_at`, `finished_at`, `exit_code`,
`status` (running/done/timeout/killed), `stdout`, `stderr` (getruncatet ~1 MB).

### `GlobalSettings` (neue Spalten, wie die LLM-Settings in der Admin-Konsole)

- `compute_enabled` (BOOL, default false — Feature-Flag)
- `compute_agents` (JSON-Liste: `[{name, url, key, gpu}]` — 1+ Compute-Server/Agenten)
- `compute_idle_timeout` (s, default 1200)
- `workspace_gpu_enabled` (BOOL)
- `compute_gpu_max_jobs` (INT, default 8 — max. parallel laufende GPU-Jobs, Rest wartet in Queue)

`.env`-Fallbacks in `config.py` für Dev.

---

## 4. Compute Agent (`compute_agent/`, neu)

FastAPI-App, eigenes Entry-Point-Script, Deployment per systemd (analog
`tutorai.service`) oder als Docker-Container mit `docker.sock`-Mount —
auf **jeder** Maschine: dem Compute-Server oder dem TutorAI-Server selbst
(Hybrid-Modus, s. §2.8).

**Endpoints** (alle nur mit gültigem signiertem Token):

| Endpoint | Zweck |
|---|---|
| `GET /health` | Erreichbarkeit + Docker-Daemon + Image-Status |
| `POST /workspaces` | erzeugen (Workspace-Spec — Agent validiert sie **erneut** als Vertrauensgrenze, s. §2.11 — + Initial-Tar der editierbaren Starter-Dateien) |
| `POST /assets/{course}/{task}` | public Dateien/Dataset auf **diesen** Agenten bringen (Upload-Tar **oder** Download-Spec `{url, limit, checksum}`) — read-only für alle Studenten, je Agenten 1× je Task-Änderung |
| `POST /assets/{course}/{task}/transform` | LLM-Transform-Skript in Einweg-Container ausführen (Input read-only, kein Netzwerk, Output gegen Manifest validiert) → Asset-Ordner (s. §7.1) |
| `DELETE /workspaces/{key}` | Container + Volume entfernen |
| `GET /workspaces/{key}/files` | Dateibaum |
| `GET/PUT/DELETE /workspaces/{key}/files/{path}` | Datei lesen/schreiben/löschen |
| `POST /workspaces/{key}/files/move` | Datei verschieben/umbenennen (`{src, dst}`, nur schreibbare Zone) |
| `POST /workspaces/{key}/exec` | Befehl ausführen; `async=false` → sync; `async=true` → `run_id` |
| `GET /workspaces/{key}/runs/{run_id}` | Status + Log-Tail + Artefakte |
| `POST /workspaces/{key}/runs/{run_id}/stop` | Job beenden |
| `GET /workspaces/{key}/snapshot` | tar des Volumes |
| `POST /taskenvs/{course}/{task}` | geteilte Task-Umgebung bauen (pip-Pakete) — 1× je Task-Änderung, read-only-Mount `/env` (s. §2.10) |

**Auth:** Agent bindet **nur auf 127.0.0.1** am Compute-Server — einziger
Zugangsweg ist der SSH-Tunnel (key-basiert, s. §2.6). Zusätzlich trägt jede
Request aus TutorAI ein HMAC-Token `{task_id, student_id, op, exp=60s}`
(gemeinsamer Key), damit Operationen auf den richtigen Workspace beschränkt
bleiben.

**Container-Verwaltung:**
- Create: `docker create --rm --read-only -v tutorai-ws-{key}:/workspace
  --cpus … --memory … --pids-limit=256 --network=none --label
  tutorai.ws={key} {image} /bin/sh -c "sleep infinity"`
- Execs: `docker exec -w /workspace … sh -c "{run_command}"`
- Reaper: Background-Task prüft letzte Aktivität (in-memory Registry),
  killt nach Idle-Timeout.
- Persistenz über Neustarts: Labels + `docker ps -a --filter
  label=tutorai.ws=` → Registry rekonstruieren.

**Docker-Images** (neuer Ordner `compute_images/` mit Dockerfiles +
Build-Skript `scripts/build_compute_images.sh`, das auf dem Compute-Server
läuft und alle Images baut):
- `tutorai/py-ml:1` — python:3.12-slim + numpy, pandas, scikit-learn,
  matplotlib, torch (CPU; GPU-Variante `py-ml-gpu:1` mit CUDA-Base)
- `tutorai/cpp:1` — gcc/clang + make, cmake, SDL2/GLFW-Header, xvfb,
  offscreen-Rendering-Support
- `tutorai/asm:1` — nasm, binutils, objdump
- `tutorai/server:1` — debian/ubuntu + nginx, sshd, bind9, ufw, curl, …
  (alles vorinstalliert, da `--network=none`)

---

## 5. TutorAI-Backend (neue/veränderte Module)

| Modul | Inhalt |
|---|---|
| `services/workspace_presets.py` | Preset-Definitionen (s. o.) |
| `services/compute_client.py` | httpx-Client zum Agenten, Token-Signierung, Health-Check, Timeouts |
| `services/workspace_service.py` | Orchestrierung: `ensure_workspace(task, student)`, Datei-Operationen (Limits: 50 MB/Datei, 512 MB/Workspace), Run-Jobs, Snapshot, `task_starter_tar(task)` (public Dateien als Initial-Tar) |
| `services/grading_service.py` | neuer Pfad `_grade_workspace(task, submission, session)` (frischer Container, private Verifikation, LLM-Feedback) |
| `api/tutor.py` | Workspace-Dateien der Aufgabe verwalten (CRUD + Multipart-Upload für Datasets, public/private), Task-Felder erweitern |
| `api/student.py` | `GET/PUT/DELETE …/workspace/files…`, `POST …/workspace/run`, `GET …/workspace/runs/{id}`; `submit` erweitert für `workspace` (Snapshot statt Code-String) |
| `api/admin.py` | Compute-Settings in der Admin-Konsole (+ Health-Check-Button "Agent erreichbar?") |
| `main.py` | Template-Auswahl je TaskType (s. §7), neue Web-Route-Parameter |

**Degradierter Modus (Agent down / nicht konfiguriert):** Workspace-Aufgaben
bleiben sichtbar und editierbar (Dateien aus DB/Disk), "Ausführen" und
"Abgeben" sind deaktiviert mit klarem Banner:
"⚠️ Compute-Server nicht erreichbar — Ausführung erst wieder möglich, wenn
er online ist." (Analoge Situation wie heute, wenn der LLM down ist.)

---

## 6. UI — Student: `task_solve_workspace.html`

Gleiche Zwei-Spalten-Struktur wie heute:

- **Linke Spalte (unverändert von Base):** Breadcrumb, Prev/Next-Navigation,
  Titel, Badges (Typ/Punkte/Versuche/Deadline), Aufgabenstellung (Markdown),
  💡 Hints, Public-Info.
- **Rechte Spalte — "Mini-IDE":**
  1. **Dateibaum** (kompakt, links neben dem Editor, aufklebbar bei
     kleinen Projekten) — Öffnen/Neu/Upload/Löschen. Geteilte Komponente
     `static/js/workspace.js` (auch in `task_detail_workspace.html`):
     Ordner-Collapse, Drag-&-Drop-Verschieben, Rechtsklick-Kontextmenü
     (Neu/umbenennen/verschieben/löschen, Main-Datei ⭐ tutor-seitig).
  2. **CodeMirror** mit Modus je Dateiendung (python/c++/asm/sh/nginx/…).
  3. **Toolbar:** ▶️ Ausführen, ⏹ Stop (bei long-running), 🧪 Verifikation
     (falls public-Test-Info existiert), ↺ Zurücksetzen auf Vorlage.
  4. **Terminal-Panel** (Mono, dunkel): stdout/stderr, Exit-Code, Dauer.
     Bei Langläufen: "⏳ läuft seit 03:24 min …" + Live-Log-Tail.
  5. **Artefakt-Galerie:** Bilder inline (Loss-Curve, Screenshot),
     andere Dateien als Liste mit Download.
  6. **Abgabe-Leiste** wie bei Code-Aufgaben (Versuche, Punkte-Progress,
     LLM-Grading-Fortschritt, Feedback-Karten).
- **Container-Status-Banner** über dem IDE: "🐳 Umgebung wird vorbereitet …"
  → "✅ bereit" / "⚠️ Compute offline".

Automatisches Speichern (debounced ~1 s nach letzter Tastenaktion) +
expliziter 💾-Button. `as_student=1`-View für Tutoren zeigt exakt dieselbe
Seite → Tutoren können jede Workspace-Aufgabe vor der Freigabe selbst testen.

## 7. UI — Tutor: `task_detail_workspace.html`

Formular wie heute, plus **zwei neue Sektionen** (nur bei Aufgabentyp
`workspace`). Die bisherigen Felder „Code-Vorlage" und „Unit-Tests" (jeweils
ein einziges Textfeld) werden durch einen **Datei-Workspace** ersetzt, der
die eigentliche Container-Dateistruktur abbildet.

### 7.0 Sektions-Layout

```
┌───────────────────────────────────────────────────────────┐
│ 🤖 LLM-Aufgabe generieren  (generiert ALLES: Preset,       │
│    Dateien, Run/Verify, Dataset) → füllt den Entwurf      │
├───────────────────────────────────────────────────────────┤
│ [1] Grundlagen   (aus Base: Titel, Beschreibung, Punkte,  │
│     Deadline, Sichtbarkeit, Hints)                        │
├───────────────────────────────────────────────────────────┤
│ [2] Arbeitsumgebung & Ressourcen   (§7.0.1)               │
├───────────────────────────────────────────────────────────┤
│ [3] Dateien & Workspace   (Datei-Manager, §7.0.2)         │
└───────────────────────────────────────────────────────────┘
```

### 7.0.1 Sektion [2] — Arbeitsumgebung: Workspace-Spec (YAML) + Quick-Fields

Die **Workspace-Spec (YAML) ist immer sichtbar und direkt editierbar**
(CodeMirror, YAML-Modus) — für maximale Flexibilität (s. §2.11). Die
Quick-Fields sind eine Komfort-Layer auf derselben Spec:

```
┌─ [2] Arbeitsumgebung ─────────────────────────────────────────┐
│  Quick-Fields:                                                │
│  Preset [Python (ML) ▾]   Run [python3 train.py        ]      │
│  Timeout [900 s ▾]        GPU [ ]     Internet [ ]           │
│  Pakete [transformers==4.40.0         ]   ⚙ abgewichen       │
│  Artefakte [results.json, *.png       ]                      │
│                                                              │
│  ┌─ workspace_spec.yaml (editierbar) ─────────────────────┐   │
│  │ preset: python-ml                                      │   │
│  │ image: tutorai/py-ml                                   │   │
│  │ working_dir: /workspace                                │   │
│  │ run: python3 train.py                                  │   │
│  │ verify:                                                │   │
│  │   command: python -m pytest .tests/ -v                 │   │
│  │ limits: { cpu: 2, memory: 4g }   ⚠ Zeile 12: Key      │   │
│  │ …                                            unbekannt │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

- **YAML ist die Source of Truth**; Quick-Fields schreiben in dieselbe
  Spec. Manuell abweichende Keys zeigen im Quick-Field „⚙ abgewichen“
  (aktueller Wert aus der Spec) statt zu überschreiben; je Feld
  „↺ auf Standard“.
- **Validierung inline** (Allowlist/Blocklist, s. §2.11/§9): Fehler mit
  Zeilenmarkierung im Editor; Speichern bei Validierungsfehler wird
  blockiert.
- Nicht kuratiertes `image` → Warn-Badge, aber erlaubt.
- `verify:` + `.tests/` sind Teil der Spec, aber für Studenten unsichtbar
  (Student-Paket lässt sie weg, s. §7.2).

### 7.0.2 Sektion [3] — Datei-Manager (das Herzstück)

Ein **Dateibaum = die reale Container-Dateistruktur**. Die Ordner-
Konvention kodiert öffentlich/privat/read-only — **keine Checkbox je
Datei**, die Position bestimmt es:

```
workspace/
├── (Dateien hier)      → public, im Student-/workspace, EDITIERBAR
├── data/               → public, read-only (Dataset/Assets) → /assets
├── .solution/          → privat, Musterlösung (Korrektur-Referenz)
└── .tests/             → privat, Verifikation/Tests (nur beim Grading injiziert)
```

```
┌─ [3] Dateien & Workspace ─────────────────────────────────┐
│  Der Student sieht genau die public-Bereiche; .solution/  │
│  .tests/ bleiben ihm unsichtbar (wie model_solution heute)│
├───────────────────────────────────────────────────────────┤
│  📁 /   (public — Student editiert)              [+ Datei] │
│   ├─ 🐍 main.py                        [●] [✎] [🗑]       │
│   └─ 🐍 train.py                       [●] [✎] [🗑]       │
│                                                           │
│  📁 data/  (public — read-only, Dataset)         [+ Upload]│
│   └─ 🗃  mnist.csv  (48 MB)                       [↺] [🗑]  │
│                                                           │
│  📁 .solution/  (privat — nur Korrektur)        [+ Datei]  │
│   └─ 🐍 solution.py                                 [✎] [🗑]│
│                                                           │
│  📁 .tests/  (privat — Verifikation)               [+ Datei]│
│   ├─ 🐍 test_main.py                                [✎] [🗑]│
│   └─ 📜 verify.sh                                   [✎] [🗑]│
└───────────────────────────────────────────────────────────┘
```

- **✎** öffnet einen CodeMirror-Editor (Drawer/Modal) mit dem korrekten
  Syntax-Modus je Dateiendung. **[●]** = Datei für public sichtbar
  markieren (Testnamen ohne Code, s. u.).
- **+ Datei** legt eine Textdatei an, **+ Upload** für Binaries/Datasets
  (Multipart, Size-Limits s. §9). **↺** = auf Preset-Vorlage zurücksetzen.
- **Public-Tests (optional):** eine Datei in `.tests/` kann als „Name
  sichtbar" markiert werden → der Student sieht die Testnamen (analog den
  heutigen Public-Tests), aber nicht den Code.
- **Speichern** synchronisiert den Baum in `TaskWorkspaceFile` + Disk (s. §3);
  große Dateien wandern in den Asset-Ordner, `.solution/`+`.tests/` landen in
  `.private/`.

**Was „evtl. noch weiteres" abdeckt:** Musterlösung (`.solution/`, privat,
als LLM-Korrektur-Referenz), Run- + Verify-Befehle, Zusatz-Pakete,
Dataset-Karte (s. §7.1), Artefakt-Liste, und die gefaltete Advanced-Sektion
für Ressourcen-Limits. Das sollte für alle vier Presets reichen — für
C++/Server kommen evtl. Makefile/`verify.sh` als normale Dateien in den Baum.

### 7.1 LLM-Generierung für Workspace-Aufgaben (Phase 3)

**Prinzip: Das LLM schlägt vor, die Infrastruktur befehligt nie.**
Die Generierung füllt das Aufgaben-Formular als **Entwurf** (Workspace-
Spec-YAML, Starter-/Test-/Lösungsdateien, Dataset-Spec). Der Tutor
prüft/adjustiert und speichert — erst der **Task-Save** löst die
Seiteneffekte aus (Taskenv-Build, Asset-Sync/Download, Transform). Das
LLM „beauftragt“ also keinen Compute-Server, es liefert nur
validierbare Daten.

**Preset-/Image-Wahl:**
- Der Prompt enthält die aktuelle Preset-Liste **datengesteuert aus
  `PRESETS`** (Name, Label, Basis-Pakete, GPU-Fähigkeit) + Regel:
  „Nutze ein vorhandenes Preset, wenn es passt; ergänze Pakete als
  versionsgepinnte requirements; erfinde keine Images.“
- Output-Felder: `preset`, `workspace_packages`, `needs_new_image`
  (bool) + `new_image_requirements` (Freitext). Letzteres ist ein
  **Hinweis für Tutor/Admin** — ein kuratiertes neues Image bleibt ein
  bewusster Ops-Schritt (s. §2.10), nie automatisch.
- **Backend-Validierung** vor dem Speichern (das LLM-Output ist
  unzuverlässige Eingabe): Preset muss in der Liste existieren, Pakete
  müssen dem Muster `[A-Za-z0-9._-]+(==[0-9][A-Za-z0-9.+-]*)?` folgen,
  Run-Befehl ist eine Zeile ohne Shell-Ketten (`;`, Backticks, `$(...)`).

**Datensätze — drei Quellen, ein Ziel (Asset-Ordner, read-only `/assets`):**

1. **Katalog (Zuverlässigkeit zuerst):** kleine kuratierte Tabelle bekannter
   Datasets (Name, URL, Checksum, Größe, Format — z. B. MNIST, CIFAR-10,
   UCI-Sätze). Das LLM soll **Katalog-Einträge bevorzugen**; freier
   URL-Fallback nur mit Hinweis.
2. **Public-Download:** LLM-Spec `{name, url, format, expected_size}` →
   beim Task-Save lädt der **Agent** (nicht der Web-Server, nicht das
   LLM) in seinen Asset-Ordner: Streaming mit Größen-Limit (z. B. 5 GB),
   Timeout, Checksum-Prüfung (wenn bekannt). Status wird getrackt wie die
   Import-Stadien im Kurs-Import: `pending → downloading → ready/failed`;
   die Aufgaben-UI zeigt „⬇️ Dataset wird geladen …“ bzw. den Fehler
   (URL unerreichbar, Checksum-Mismatch) — Tutor korrigiert, Retry-Button.
3. **Hochgeladene Datei passend machen:** Tutor lädt eigene Datei über den
   Datei-Manager hoch, Button „🤖 Für Aufgabe aufbereiten“ → das LLM
   schreibt ein **Transform-Skript** (pandas/etc.) + ein
   **Erwartungs-Manifest** (Output-Dateien, Format, z. B. Zeilenanzahl-
   Spanne). Das Skript läuft in einem **Einweg-Container** des Agents
   (kein Netzwerk, Input read-only, Output in den Asset-Ordner,
   CPU/RAM-Limits) — das LLM berührt nie selbst das Dateisystem. Der
   Output wird gegen das Manifest validiert; der Tutor sieht eine
   Vorschau (Dateiliste, `head`-Auszug) und nimmt sie an, bevor die
   Aufgabe für Studenten freigeschaltet wird.

**Multi-Agent-Feinheit:** Assets leben je Agenten-Host. Bei mehreren
Agenten wird der Asset-Status **je (task, agent)** getrackt und lazy
synchronisiert, wenn der erste Workspace der Aufgabe auf diesem Agenten
startet (s. §2.9/§2.8).

### 7.2 Download-Pakete: Lösung & Aufgabe lokal ausführen

Die Spec ist die „docker-compose der Aufgabe“ → ein **Compose-Generator**
(`services/compose_gen.py`) rendert daraus ein lauffähiges
`docker-compose.yml` für den lokalen Rechner (Bind-Mounts auf den
Paketordner). Das Image wird **lokal gebaut** aus einem öffentlichen
Basis-Image + der dünnen Ableger-Schicht (pinned `requirements.txt` im
Paket) — auf unseren Servern liegen **keine großen Images**, und von dort
wird auch nichts gepullt.

**Student-Paket** — Button „⬇️ Lösung + Umgebung herunterladen“ in
`task_solve_workspace.html` (aktueller Stand, zusätzlich je Einreichung
in der Historie):
- ihre Workspace-Dateien (ihr Code)
- `data/` (public Datasets — nötig zum lokalen Ausführen; bei sehr
  großen Datasets optional weglasbar: Checkbox „ohne Daten“)
- generiertes `docker-compose.yml` (baut das Image via
  `images/Dockerfile`: `FROM <öffentliches Basis-Image>` +
  `pip install -r requirements.txt`) + `README.md`
  (Voraussetzungen: Docker + Internet; bei GPU: NVIDIA-Treiber +
  nvidia-container-toolkit; Start: `docker compose run workspace`;
  Hinweis: erster Build dauert wenige Minuten — die dicke Schicht, z. B.
  torch, kommt aus PyPI, nicht von unserem Server; danach gecacht)
- **nie enthalten:** `.solution/`, `.tests/`, `verify:`-Block

**Tutor-Paket** — Button „⬇️ Gesamtpaket“ in `task_detail_workspace.html`
und je Einreichung auf der Review-Seite („⬇️ Studenten-Version“: Dateien
des Studenten statt Template):
- alles: Starter-Template, `data/`, `.solution/`, `.tests/`
- `docker-compose.yml` mit **zwei Services**: `workspace` (Run) und
  `verify` (`docker compose run --rm verify`)
- `README.md` inkl. Verifikations-Anleitung + Aufgaben-Metadaten
  (Punkte, Aufgabenstellung)

**Endpoints:** `GET /api/student/…/tasks/{id}/package` (+
`/submissions/{id}/package`), `GET /api/tutor/…/tasks/{id}/package`
(+ `/submissions/{id}/package`). Tar.gz wird on-the-fly gebaut
(kurz-gecacht), Größenanzeige vor dem Download.

**Kein Registry-Bedarf, keine großen Images von unserem Server:**
Studenten/Tutoren bauen die Umgebung lokal aus dem öffentlichen Basis-
Image (Docker Hub, klein) + der dünnen Ableger-Schicht (pinned
`requirements.txt` im Paket; die Pakete — z. B. torch — kommen von
PyPI). Unser Server liefert nur das kleine Tar.gz (Code, Datasets,
Dockerfile, Compose, README). GPU-Aufgaben: `FROM nvidia/cuda:…-runtime`
(öffentlich, Docker Hub) + torch-CUDA-Wheels aus PyPI. Einziger Aufwand:
der erste `docker compose build` dauert wenige Minuten (Downloads aus
PyPI), danach ist das Image lokal gecacht (s. §2.9).

## 8. Template-Umstrukturierung (Grund- + Spezial-Templates)

Ja — wird empfohlen, aber als **eigener, riskarmer Schritt vor** der
Workspace-Funktionalität (reines Refactoring, Verhalten unverändert).

Heute: `task_solve.html` (~2300 Zeilen) und `task_detail.html` sind Monolithe
mit Typ-Branches (`{% if is_code %}` etc.).

**Zielstruktur** (Jinja-Erbschaft, wie bereits `course/base.html`):

```
templates/
  student/
    task_solve_base.html        # NEU: Header, Prev/Next, Badges, Punkte-Karte,
                                #       Aufgabenstellung, Hints, Feedback-Bereich,
                                #       Abgabe-Leiste, geteiltes JS (Timer, Hints,
                                #       Motivation, Grading-Polling)
                                #       Blocks: solution_area, solve_scripts
    task_solve_text.html        # aus task_solve.html: Markdown-Editor,
                                #       Foto/Zeichen-Canvas, LaTeX-Upload
    task_solve_code.html        # aus task_solve.html: CodeMirror, Console,
                                #       Plots, Run/Test-Buttons
    task_solve_workspace.html   # NEU: Mini-IDE (s. §6)
  tutor/
    task_detail_base.html       # NEU: LLM-Generierung, Titel/Beschreibung,
                                #       Punkte/Deadline/Sichtbarkeit/Hints
                                #       Block: type_section
    task_detail_text.html       # (heute fast leerer Typ-Teil)
    task_detail_code.html       # Code-Vorlage + Unit-Tests
    task_detail_workspace.html  # NEU: Arbeitsumgebung (s. §7)
```

**Template-Auswahl in `main.py`** (aktuell L1518–1521):

```python
student_tpl = f"student/task_solve_{task.task_type.value}.html"
# Fallback: existiert die Datei nicht → task_solve_base.html (oder _text)
```

**Schritte (jeder einzeln testbar):**
1. `task_solve_base.html` extrahieren; `task_solve_text.html` +
   `task_solve_code.html` ableiten — reines Verschieben, kein Verhalten
   ändert sich.
2. `task_detail_base.html` analog.
3. Testen (manuell: Text- + Code-Aufgabe, Student- + Tutor-View,
   `as_student=1`).
4. Erst dann `task_solve_workspace.html` / `task_detail_workspace.html`
   neu dazu.

**CodeMirror-Modi** (vendor-mirror `static/vendor/codemirror/mode/`
ergänzen, gitignore-pflichtfrei wie die anderen): `clike` (C++), `shell`,
`nginx`, kleiner `nasm`-Overlay-Modus (oder `shell` als Fallback).

---

## 9. Sicherheit

- Container: read-only Root-FS, `--network=none` (Default), keine
  Privileges, `--pids-limit`, Memory/CPU-Limits, kein Zugriff auf
  `docker.sock` außerhalb des Agenten.
- Agent: bindet nur auf 127.0.0.1 am Compute-Server; einziger Zugangsweg =
  SSH-Tunnel (Key-basiert, kein Password), dazu HMAC-Op-Tokens mit 60-s-
  Expiry, Rate-Limits, Log-Output pro Workspace.
- **Workspace-Spec-YAML:** Key-**Allowlist** + **Blocklist** (s. §2.11);
  Validierung (1) beim Speichern in der UI, (2) am Agenten
  (Vertrauensgrenze). Multi-Service, Ports, Host-Mounts, Privileges:
  nicht möglich.
- Dateien: Pfade sanitizen (kein `..`), Size-Limits (50 MB/Datei,
  512 MB/Workspace), Upload-Typ-Checks, keine Symlinks aus Extracts.
- Private Dateien: nie im Student-Volume, nie via TutorAI-API an Studenten
  (`is_public=false` wird hart gefiltert — wie `model_solution` heute).
- Datasets: Download nur für Kurs-Mitglieder; Sichtbarkeit folgt der
  Aufgabe (wie Medien heute).
- GPU: opt-in pro Aufgabe + global; eigener Scheduler-Queue in Phase 3.

---

## 10. Phasenplan

### Phase 0 — Fundament (keine neue Funktion)
1. Template-Refactoring s. §8 (Schritte 1–3), manuell getestet.
2. Config: `COMPUTE_*`-Variablen + `GlobalSettings`-Spalten, Feature-Flag
   default **aus**.
3. `compute_images/`-Ordner: Dockerfile `py-ml` + Build-Skript; Agent-Manuskript
   für ein Ziel-Server-OS.

### Phase 1 — MVP: ein Preset (python-ml), Agent lokal oder auf 1 Server
1. Dockerfile für die TutorAI-App + `deploy/compose.local.yml`
   (tutorai + compute-agent + Daten-Volumes); `deploy.sh`-Anpassung.
2. `compute_agent/` (FastAPI) + Docker-Verwaltung + Reaper.
3. Datenmodell (Task-Spalten, `TaskWorkspaceFile`, `Submission.workspace_snapshot`,
   `WorkspaceRun`) + Migration.
4. `workspace_service.py` + `compute_client.py`.
5. API: Tutor-Datei-Manager, Student-Workspace-Dateien/Run/Submit.
6. UI: `task_solve_workspace.html` (Mini-IDE), `task_detail_workspace.html`
   (inkl. YAML-Editor + Quick-Fields für die Workspace-Spec).
7. Grading: `_grade_workspace` (frischer Container + Verifikation + LLM).
8. **Test mit einer echten Aufgabe:** kleines NN-Training (z. B.
   MNIST-Teilmenge, ~2–5 min Laufzeit) inkl. Loss-Curve als Artefakt.

### Phase 2 — Produktion Multi-Server
1. Agent auf Compute-Servern deployen (Compose oder systemd, bind
   127.0.0.1) + SSH-Tunnel-Unit auf dem TutorAI-Server; Admin-Konsole zeigt
   Tunnel-/Agent-Status.
2. Health-Check + degradierter Modus im UI; Admin-Konsole: Compute-Settings
   + "Verbindung testen".
3. Presets `cpp`, `asm`, `server` + Images + CodeMirror-Modi.
4. Long-running-UX verfeinern (Live-Log-Tail, Stop, Timeout-Anzeige).
5. Server-Preset: `verify.sh`-Probing-Muster (Dienst starten, curl/ss prüfen).
6. Download-Pakete (Student + Tutor, s. §7.2) — lokal gebaut aus
   öffentlichen Basis-Images + dünner Ableger-Schicht, kein
   Registry-Bedarf.

### Phase 3 — Komfort & Scale-out
- LLM-Generierung für Workspace-Aufgaben (s. §7.1): Preset-/Paket-Vorschlag
  mit Backend-Validierung, Starter-Dateien + Verifikation, Dataset-Katalog,
  Public-Download via Agent, Transform für hochgeladene Dateien (Einweg-
  Container + Manifest-Prüfung + Tutor-Vorschau).
- Tutor: Submissions **neu ausführen** (Snapshot in frischer Container),
  Lauf-Historie in der Review-Seite.
- GPU-Queue (FIFO, Status "wartet auf GPU"), Multi-Server-Balancing.
- Course-level Compute-Settings (via `settings_resolver`).
- Multi-File-C++-Projekte mit Makefile-Preset; `internet: true`-Option.

---

## 11. Entscheide (Stand der Klärung, 2026-07-02)

1. **GPU:** Compute-Server mit mehreren A40/A100 (> 100 GB RAM); Aufgaben
   gehen überwiegend um kleine CNNs auf MNIST (1–2 GB VRAM je Training) →
   GPU-Passthrough je Aufgabe + Queue (`compute_gpu_max_jobs`, Default ~8;
   GPU bewusst nicht voll auslasten), CUDA-Image `py-ml-gpu:1`.
2. **Netzwerk:** getrennte Netze, kein direkter Pfad → permanenter
   **SSH-Tunnel** (Port-Forward, Key-Auth) vom TutorAI-Server zum
   Compute-Server; Agent nur auf 127.0.0.1 (s. §2.6).
3. **Compute-Server:** beliebiges Linux; Docker + NVIDIA-Treiber/
   nvidia-container-toolkit werden installiert → Setup-Skript + systemd-Units
   im Repo.
4. **Konkurrenz:** max. ~30–40 gleichzeitig → Preset-Limits, GPU-Queue;
   ein Rechner (≥ 128 GB RAM) reicht vermutlich aus, Skalierung über die
   Multi-Agent-Registry (bereits eingeplant).
5. **Internet in Containern:** ja, **opt-in je Aufgabe**
   (`internet:` in der Workspace-Spec, bridge + egress-Filter) — explizit
   vom Tutor frei geschaltet, z. B. für `pip install`/`apt`.
6. **Hybrid lokal + remote:** Kleine Aufgaben laufen direkt auf dem
   TutorAI-Server (gleicher Docker-Daemon, kein Tunnel); GPU-Aufgaben routen
   über die Registry auf den Compute-Server (s. §2.8). Routing v1:
   GPU-Flag → gpu-fähiger Agent, sonst lokaler Agent.
7. **Deployment:** Auch die TutorAI-App wird containerisiert; lokales
   Hybrid-Setup = eine Compose-File (tutorai + Agent + Daten-Volumes),
   Compute-Server mit eigenem Docker/Agenten. Images werden **einmalig pro
   Node** gebaut/gezogen und dann gecacht — kein Download pro Student/Aufgabe
   (s. §2.9).
8. **Workspace-Spec (YAML) als Single Source of Truth:** Der Tutor sieht
   und editiert das komplette „docker-compose der Aufgabe“; die
   Quick-Fields sind eine Komfort-Layer. Agent (Server) und die lokalen
   Download-Pakete interpretieren dieselbe Spec (s. §2.11).
9. **Download-Pakete für lokale Ausführung:** Student erhält Lösung +
   Datasets + Compose (ohne private Bereiche), Tutor das Gesamtpaket inkl.
   `.solution/` + `.tests/` + verify-Service — läuft lokal per
   `docker compose` (s. §7.2).
10. **Read/Write-Modell fix:** Read-only by default (Image-Root, `/env/`,
    `/assets/`); einziger schreibbarer Ort `/workspace`. Keine generische
    Permission-DSL in der Spec — zusätzliche `mounts:` sind immer
    read-only (s. §2.3).
11. **Bewertungs-Datenfluss:** Der Agent liefert strukturierte Ergebnisse
    (Exit-Code, Ausgabe, Artefakte) per API; der LLM läuft auf der
    TutorAI-Seite und erhält nur Aufgabenstellung + Studenten-Quelldateien
    + Testausgabe + Artefakte — nie Bibliotheken/Datasets/Container-Inneres
    (s. §2.5).
12. **Keine (großen) Images von unserem Server für lokale Ausführung:**
    Download-Pakete enthalten nur das öffentliche Basis-Image als `FROM`
    + die dünne Ableger-Schicht als Dockerfile + pinned `requirements.txt`
    (Pakete kommen aus PyPI, Docker-Hub-Images werden direkt gezogen).
    Kein Registry-Bedarf (s. §2.9/§7.2).

### Restliche Kleinigkeiten (bei Umsetzung klären)

- Ob der TutorAI-Server schon Docker hat (sonst Installation + User-Rechte)
- SSH-Zugang zum Compute-Server (User, Key-Hinlegung) für das Tunnel-Setup
- Soll der Student sehen, auf welcher Maschine sein Workspace läuft?
  (Empfehlung: nein — abstrahieren)

---

## 12. Abgrenzung / Bewusst NICHT

- Keine generische "IaC-Cloud" (keine Multi-OS-Auswahl, kein arbitrary
  Image für Studenten) — Kuratierte Presets; der Tutor darf als
  Vertrauensquelle ein eigenes `image:` setzen (Warn-Badge, s. §2.11).
- Kein Web-Terminal (Shell) für Studenten — nur Datei-Editor + definierter
  Run-Befehl. (Offen: evtl. Phase 3, falls "Server aufsetzen" es erfordert.)
- Keine Live-Video-/Screen-Share-Ansicht der Container.
- PostgreSQL-Migration der neuen Tabellen bleibt Teil der bestehenden
  `migrate_schema()`-Konvention.
