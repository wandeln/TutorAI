# Deployment: TutorAI + Compute-Agent

Drei Betriebsarten — alle kombinierbar:

| Modus | Wo läuft was | Für was |
|---|---|---|
| **A. Docker-Hybrid** | TutorAI + Agent in einer `docker compose` auf EINER Maschine | MVP, kleine Kurse, kleine Aufgaben |
| **B. Nativ-Systemd** | TutorAI wie bisher (uvicorn + nginx), Agent als zweite Unit auf demselben Host | Bestehende Produktiv-Installation (hochficht) |
| **C. Multi-Server** | TutorAI + lokaler Agent wie A/B; weitere Agenten auf Compute-Servern (nativ ODER als Docker-Container), verbunden per SSH-Tunnel | GPU-Trainings, Skalierung |

Der Agent spricht immer dieselbe API (`/health`, `/workspaces`, `/assets`,
`/tasks/{course}/{task}/init-build`, …). TutorAI kennt die Compute-Engines über die **Engine-Registry**
in der Admin-Konsole (Systemeinstellungen → Compute/Workspace) — dort stehen
Name, URL, Key und der GPU-Zugang (welche der von der Engine gemeldeten GPUs
erlaubt sind, per Checkbox in der UI; Default: alle). Alle Container einer
Engine starten automatisch mit den erlaubten GPUs. Routing: erste gesunde
Engine des Engine-Pools der Aufgabe (lokal bevorzugt); keine gesunde Engine
→ klarer Fehler, kein stilles Fallback.

---

## Modus A — Docker-Hybrid (eine Maschine)

```bash
# Voraussetzungen: Docker, .env im Repo-Root
export COMPUTE_AGENT_KEY=<identisch mit COMPUTE_AGENT_KEY in der .env>
docker compose -f deploy/compose.local.yml up --build -d

docker compose -f deploy/compose.local.yml logs -f tutorai        # Web-App
docker compose -f deploy/compose.local.yml logs -f compute-agent  # Agent
```

- TutorAI: `http://<host>:8000` (Port in `compose.local.yml` änderbar).
- Agent: nur im Compose-Netz (`http://compute-agent:8700`) + optional
  `127.0.0.1:8700` auf dem Host. **Registry leer lassen** → das
  `.env`-Fallback (`COMPUTE_AGENT_URL=http://127.0.0.1:8700`) wird
  überschrieben von der Compose-Env (`http://compute-agent:8700`).
  Falls eine Registry konfiguriert ist, dort `http://compute-agent:8700`
  eintragen (für den TutorAI-Container).
- Der Agent verwaltet den **Host-Docker-Daemon** (docker.sock-Mount):
  Workspace-Container, Volumes und Images leben auf dem Host.
- GPU: `nvidia-container-toolkit` auf dem Host installieren. Der Agent
  meldet die verfügbaren GPUs per Health (`gpus: [0, 1, …]`); der
  GPU-Zugang wird je Engine in der Engine-UI gewählt (Checkboxen;
  Default alle, „keine“ = nur CPU).

## Modus B — Nativ-Systemd (Bestand)

Bestehende Installation (uvicorn via `tutorai.service` + nginx) bleibt
unverändert. Der Agent läuft als zweite Unit auf demselben Host:

```bash
# Als root (REPO-Pfad der Installation, z. B. /home/wandel/Projects/TutorAI)
sudo useradd --system --create-home tutorai
sudo mkdir -p /etc/tutorai /opt/tutorai/assets
sudo cp deploy/compute-agent.env.example /etc/tutorai/compute-agent.env
sudo nano /etc/tutorai/compute-agent.env          # AGENT_KEY == COMPUTE_AGENT_KEY (.env)

# Agent-Venv (oder bestehendes venv/Anaconda nutzen — ExecStart dann anpassen)
sudo python3 -m venv /opt/tutorai/venv
sudo /opt/tutorai/venv/bin/pip install -r compute_agent/requirements.txt

# Unit: User= + WorkingDirectory= + ExecStart= auf die Installation anpassen
sudo cp deploy/tutorai-compute-agent.service /etc/systemd/system/
sudo nano /etc/systemd/system/tutorai-compute-agent.service
sudo systemctl daemon-reload && sudo systemctl enable --now tutorai-compute-agent
sudo journalctl -u tutorai-compute-agent -f
```

Der Agent bindet auf `127.0.0.1:8700` → genau die `.env`-Default-URL
(`COMPUTE_AGENT_URL`), kein Tunnel nötig.

#### Alternative: Agent als Docker-Container (ohne eigene Systemd-Unit)

Für reine Compute-Server ohne TutorAI-Installation gibt es
`deploy/compose.compute-only.yml` — der Agent läuft dann als Container,
verwaltet aber weiterhin den **Host-Docker-Daemon** (docker.sock-Mount):

```bash
# Auf dem Compute-Server (beliebiges Linux, beliebiges User-Setup):
# 1) Docker installieren (GPU: + nvidia-container-toolkit)
# 2) Repo hinbekommen (git clone / rsync), z. B. /srv/TutorAI
# 3) Key hinterlegen — exakt derselbe Key, der in der Engine-Registry
#    des TutorAI-Servers für diese Engine eingetragen ist:
sudo sh -c 'echo "AGENT_KEY=<Key>" > deploy/.env && chmod 600 deploy/.env'
# 4) Starten:
docker compose -f deploy/compose.compute-only.yml up -d --build
curl -s http://127.0.0.1:8700/health   # ohne Token: 401 = Auth aktiv (gut)
```

Ohne `AGENT_KEY` verweigert der Agent-Container den Start (bewusste
Schutzsperre). GPU-/Queue-Parameter (`GPU_ENABLED`, `GPU_MAX_JOBS`, …) können
in `deploy/.env` gesetzt oder direkt in der Compose-Datei angepasst werden.
Update nach Code-Änderungen: Repo aktualisieren →
`docker compose -f deploy/compose.compute-only.yml up -d --build`.
Der Agent bindet auf `127.0.0.1:8700` → für Schritt 2 (SSH-Tunnel) gilt
wie bei der nativen Variante.

## Modus C — Multi-Server (Compute-Server + SSH-Tunnel)

### 1. Compute-Server vorbereiten (einmalig pro Server)

Auf dem Compute-Server (beliebiges Linux mit Docker oder Debian/Ubuntu
Packages):

```bash
# Repo hinbekommen (git clone / rsync) und dann:
sudo ./deploy/setup_compute_server.sh /pfad/zu/TutorAI
```

Das Skript: installiert Docker + nvidia-container-toolkit (falls GPU),
legt User/Verzeichnisse/venv an und installiert die
`tutorai-compute-agent.service` (startet den Agent noch NICHT). Danach:

```bash
sudo nano /etc/tutorai/compute-agent.env   # AGENT_KEY setzen
sudo systemctl start tutorai-compute-agent
curl -s http://127.0.0.1:8700/health      # → {"ok": true, "docker": true, ...}
```

### 2. SSH-Tunnel auf dem TutorAI-Server

```bash
# Key + Publikey auf dem Compute-Server (User tutorai)
sudo ssh-keygen -t ed25519 -f /etc/tutorai/compute_ssh_key -N ""
sudo ssh-copy-id -i /etc/tutorai/compute_ssh_key.pub tutorai@compute-server-1

# Unit: COMPUTE_HOST ersetzen, ggf. lokalen Port (8701) bei mehreren Servern
sudo cp deploy/tutorai-compute-tunnel.service /etc/systemd/system/
sudo nano /etc/systemd/system/tutorai-compute-tunnel.service   # COMPUTE_HOST → Hostname
sudo systemctl daemon-reload && sudo systemctl enable --now tutorai-compute-tunnel

# Test: Agent hinter dem Tunnel
curl -s http://127.0.0.1:8701/health
```

Mehrere Compute-Server: eine Tunnel-Unit pro Server (lokale Ports 8701,
8702, …).

### 3. Registry in der Admin-Konsole

Admin → Systemeinstellungen → **Compute/Workspace**:

```
Name: local       URL: http://127.0.0.1:8700   [🧪 Test]
Name: compute-1   URL: http://127.0.0.1:8701   Key: …     [🧪 Test]
Name: compute-2   URL: http://127.0.0.1:8702   Key: …     [🧪 Test]
```

Workspace-Aufgaben sind immer verfügbar (kein Feature-Flag). Je Engine wird
der GPU-Zugang in der Engine-UI gewählt (Checkboxen für die von der Engine
gemeldeten GPUs; Default alle, „keine“ = nur CPU). Speichern ist
automatisch (Auto-Save).
Der Statusbereich zeigt die Health aller Engines (30-s-Cache).

### Betriebsprüfung

- **Tunnel down?** Agent im Status rot; Workspace-Aufgaben bleiben sichtbar,
  „Ausführen/Abgeben“ ausgegraut („⚠️ Compute-Server nicht erreichbar“).
  Der Tunnel stellt sich selbst wieder her (`Restart=always` +
  `ServerAliveInterval`), TutorAI braucht nicht neu gestartet zu werden.
- **Agent neugestartet?** Lauffähige Workspaces (Volumes) bleiben erhalten;
  laufende Jobs sind nach dem Neustart „killed“ (UI zeigt das an).
- **Image-Spec geändert?** Neuer Content-Hash → neues Tag → beim nächsten
  Installieren werden nur die geänderten Schichten neu gebaut.
  (Das `LOGICAL_IMAGES` in `compute_agent/config.py` ist nur noch Legacy-
  Fallback für Tasks aus Zeiten vor dem Image-Spec-System.)

---

## Sicherheit (Kurzfassung, s. Plan §9)

- Agent bindet nur auf `127.0.0.1` (nativ) bzw. nur im Compose-Netz (Docker).
  Einziger externer Zugangsweg: SSH-Tunnel mit Key-Auth.
- Jede TutorAI→Agent-Request trägt ein HMAC-Op-Token (60 s, scopet auf
  Workspace/Task). Key leer = offen (NUR Entwicklung!).
- Workspace-Container: read-only Root-FS, `--network=none` (Default;
  `internet: true` in der Spec = opt-in Bridge), CPU/RAM/PID-Limits,
  einziger schreibbarer Ort `/workspace`.
- Private Dateien (`.solution/`, `.tests/`) liegen nur auf der TutorAI-Disk
  und werden ausschließlich beim Grading in einen frischen Container
  injiziert — nie ins Student-Volume.

## Backups & Datenorte

| Daten | Ort (nativ) | Ort (Docker-Hybrid) |
|---|---|---|
| DB + Uploads + Task-Dateien + Snapshots | `<repo>/data/` | `<repo>/data/` (Volume) |
| Assets (Daten, Tests, Task-Skripte) | `/opt/tutorai/assets/` | Named Volume `compute-assets` |
| Task-Images (init.sh-Builds) | Docker-Images `tutorai/task/*` | Docker-Images `tutorai/task/*` (Host) |
| Student-Volumes | Docker-Volumes `tutorai-ws-*` | Docker-Volumes `tutorai-ws-*` (Host) |
