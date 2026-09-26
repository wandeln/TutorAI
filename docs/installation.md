# AICampus — Installation (ausführlich)

Alle Installations- und Betriebsarten im Detail: nativ (systemd), Docker,
Compute-Agent (lokal/remote, Multi-Server), SSH-Tunnel, GPU, Backups.

> **Schnellstart** (Docker, eine Maschine) steht in der [README](../README.md).
> Diese Doku deckt die restlichen Fälle und die Feinkonfiguration ab.

## Überblick: Betriebsarten

| Modus | Wo läuft was | Für was |
|---|---|---|
| **A. Docker-Hybrid** | AICampus + Agent in einer `docker compose` auf EINER Maschine | MVP, kleine Kurse, kleine Aufgaben |
| **B. Nativ-Systemd** | AICampus (uvicorn + nginx), Agent als zweite Unit auf demselben Host | Bestehende Produktiv-Installation |
| **C. Multi-Server** | AICampus + lokaler Agent wie A/B; weitere Agenten auf Compute-Servern (nativ ODER als Docker-Container), verbunden per SSH-Tunnel | GPU-Trainings, Skalierung |

Der Compute-Agent spricht immer dieselbe API (`/health`, `/workspaces`,
`/assets`, `/tasks/{course}/{task}/init-build`, …). AICampus kennt die
Compute-Engines über die **Engine-Registry** in der Admin-Konsole
(Systemeinstellungen → Compute/Workspace) — dort stehen Name, URL, Key und
der GPU-Zugang (welche der von der Engine gemeldeten GPUs erlaubt sind,
per Checkbox; Default: alle). Alle Container einer Engine starten
automatisch mit den erlaubten GPUs. **Routing:** erste gesunde Engine des
Engine-Pools der Aufgabe (lokal bevorzugt); keine gesunde Engine → klarer
Fehler, kein stilles Fallback.

Workspace-Aufgaben sind **immer verfügbar** (kein Feature-Flag) — ohne
erreichbare Engine degradieren die Views sauber.

---

## 1. Voraussetzungen

- Linux (Debian/Ubuntu empfohlen) mit Python 3.11+ (nativ) bzw.
  Docker + Docker Compose v2 (Docker-Modi).
- Ein **OpenAI-kompatibles LLM-Endpoint** ([Konfiguration](configuration.md#llm)).
- Für Workspace-Aufgaben: Docker auf dem Host des Agents; für GPU:
  `nvidia-container-toolkit` auf dem Host.
- (Optional) LDAP-Server für Uni-Accounts.

## 2. Nativ (ohne Docker)

### 2.1 Entwicklungsstart

```bash
cd AICampus
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # LLM-Endpoint, Secrets setzen
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 2.2 Produktiv: systemd + nginx

`aicampus.service` und `nginx.conf` liegen im Repo-Root.

```bash
# Service anpassen (User=, WorkingDirectory=, ExecStart= auf die Installation)
sudo cp aicampus.service /etc/systemd/system/
sudo nano /etc/systemd/system/aicampus.service

# nginx: SSL-Terminierung + Reverse-Proxy auf 127.0.0.1:8000
sudo cp nginx.conf /etc/nginx/sites-available/aicampus
sudo ln -s /etc/nginx/sites-available/aicampus /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo systemctl daemon-reload
sudo systemctl enable --now aicampus
journalctl -u aicampus -f
```

Beim **ersten Start** wird bei leerer DB automatisch ein Admin-Account
angelegt (`admin` / `admin`) — **Passwort nach dem ersten Login ändern**.

## 3. Modus A — Docker-Hybrid (eine Maschine)

```bash
# Voraussetzungen: Docker, .env im Repo-Root
cd AICampus
export COMPUTE_AGENT_KEY=<identisch mit COMPUTE_AGENT_KEY in der .env>
docker compose -f deploy/compose.local.yml up --build -d

docker compose -f deploy/compose.local.yml logs -f aicampus        # Web-App
docker compose -f deploy/compose.local.yml logs -f compute-agent   # Agent
```

Wichtige Punkte:

- AICampus: `http://<host>:8000` (Port in `compose.local.yml` änderbar;
  Default bindet auf `127.0.0.1` — für LAN-Zugriff auf `"8000:8000"` ändern).
- Agent: nur im Compose-Netz (`http://compute-agent:8700`) + optional
  `127.0.0.1:8700` auf dem Host. In der Engine-Registry für den
  AICampus-Container `http://compute-agent:8700` eintragen (die
  `.env`-Fallback-URL `http://127.0.0.1:8700` ist **im
  Container-Modus nicht erreichbar**).
- `AGENT_KEY` wird per **Shell-Export oder `deploy/.env`** übergeben —
  Compose-Interpolation liest `env_file` **nicht**.
- Der Agent verwaltet den **Host-Docker-Daemon** (docker.sock-Mount):
  Workspace-Container, Volumes und Images leben auf dem Host.
- `data/` wird als Volume gemountet (`/app/data`) — DB, Uploads,
  Workspaces, Submissions.
- **GPU:** `nvidia-container-toolkit` auf dem Host installieren. Der Agent
  meldet die verfügbaren GPUs per Health (`gpus: [0, 1, …]`); der
  GPU-Zugang wird je Engine in der Engine-UI gewählt (Checkboxen;
  Default alle, „keine" = nur CPU).

### Entwicklungsmodus (Live-Code + Auto-Reload)

`deploy/compose.dev.yml` ist ein Overlay, das das Repo live in den
AICampus-Container bindet und uvicorn mit `--reload` startet:

```bash
docker compose -f deploy/compose.local.yml -f deploy/compose.dev.yml up -d aicampus
```

- Python-Änderungen → App startet automatisch neu (watchfiles);
  Templates/CSS/JS greifen sofort, ohne Rebuild oder Neustart
  (`AICAMPUS_DEV=1` lässt das statische `?v=` aus der Mtime kommen).
- `--build` fällt komplett weg — nur nach `requirements.txt`-Änderungen
  oder beim allerersten Setup: `… up -d --build aicampus`.
- `compute-agent` bleibt unberührt (Agent-Änderungen wie üblich mit
  `--build compute-agent`).
- Zurück zum Normalbetrieb: `docker compose -f deploy/compose.local.yml up -d aicampus`
  (der Container wird ohne Overlay neu angelegt).

## 4. Modus B — Compute-Agent nativ (Systemd)

Bestehende nativ laufende AICampus-Installation: der Agent läuft als
zweite Unit auf demselben Host.

```bash
# Als root (REPO-Pfad der Installation, z. B. /home/wandel/Projects/AICampus)
sudo useradd --system --create-home aicampus
sudo mkdir -p /etc/aicampus /opt/aicampus/assets
sudo cp deploy/compute-agent.env.example /etc/aicampus/compute-agent.env
sudo nano /etc/aicampus/compute-agent.env          # AGENT_KEY == COMPUTE_AGENT_KEY (.env)

# Agent-Venv (oder bestehendes venv/Anaconda nutzen — ExecStart dann anpassen)
sudo python3 -m venv /opt/aicampus/venv
sudo /opt/aicampus/venv/bin/pip install -r compute_agent/requirements.txt

# Unit: User= + WorkingDirectory= + ExecStart= auf die Installation anpassen
sudo cp deploy/aicampus-compute-agent.service /etc/systemd/system/
sudo nano /etc/systemd/system/aicampus-compute-agent.service
sudo systemctl daemon-reload && sudo systemctl enable --now aicampus-compute-agent
sudo journalctl -u aicampus-compute-agent -f
```

Der Agent bindet auf `127.0.0.1:8700` → genau die `.env`-Default-URL
(`COMPUTE_AGENT_URL`), kein Tunnel nötig.

### Alternative: Agent als Docker-Container (ohne eigene Systemd-Unit)

Für reine Compute-Server ohne AICampus-Installation gibt es
`deploy/compose.compute-only.yml` — der Agent läuft dann als Container,
verwaltet aber weiterhin den **Host-Docker-Daemon** (docker.sock-Mount):

```bash
# Auf dem Compute-Server (beliebiges Linux, beliebiges User-Setup):
# 1) Docker installieren (GPU: + nvidia-container-toolkit)
# 2) Repo hinbekommen (git clone / rsync), z. B. /srv/AICampus
# 3) Key hinterlegen — exakt derselbe Key, der in der Engine-Registry
#    des AICampus-Servers für diese Engine eingetragen ist:
sudo sh -c 'echo "AGENT_KEY=<Key>" > deploy/.env && chmod 600 deploy/.env'
# 4) Starten:
docker compose -f deploy/compose.compute-only.yml up -d --build
curl -s http://127.0.0.1:8700/health   # ohne Token: 401 = Auth aktiv (gut)
```

Ohne `AGENT_KEY` verweigert der Agent-Container den Start (bewusste
Schutzsperre). GPU-/Queue-Parameter (`GPU_ENABLED`, `GPU_MAX_JOBS`, …)
können in `deploy/.env` gesetzt oder direkt in der Compose-Datei
angepasst werden. Update nach Code-Änderungen: Repo aktualisieren →
`docker compose -f deploy/compose.compute-only.yml up -d --build`.

## 5. Modus C — Multi-Server (Compute-Server + SSH-Tunnel)

### 5.1 Compute-Server vorbereiten (einmalig pro Server)

Auf dem Compute-Server (beliebiges Linux mit Docker oder Debian/Ubuntu
Packages):

```bash
# Repo hinbekommen (git clone / rsync) und dann:
sudo ./deploy/setup_compute_server.sh /pfad/zu/AICampus
```

Das Skript: installiert Docker + nvidia-container-toolkit (falls GPU),
legt User/Verzeichnisse/venv an und installiert die
`aicampus-compute-agent.service` (startet den Agent **noch NICHT**). Danach:

```bash
sudo nano /etc/aicampus/compute-agent.env   # AGENT_KEY setzen
sudo systemctl start aicampus-compute-agent
curl -s http://127.0.0.1:8700/health      # → {"ok": true, "docker": true, ...}
```

### 5.2 SSH-Tunnel auf dem AICampus-Server

```bash
# Key + Publikey auf dem Compute-Server (User aicampus)
sudo ssh-keygen -t ed25519 -f /etc/aicampus/compute_ssh_key -N ""
sudo ssh-copy-id -i /etc/aicampus/compute_ssh_key.pub aicampus@compute-server-1

# Unit: COMPUTE_HOST ersetzen, ggf. lokalen Port (8701) bei mehreren Servern
sudo cp deploy/aicampus-compute-tunnel.service /etc/systemd/system/
sudo nano /etc/systemd/system/aicampus-compute-tunnel.service   # COMPUTE_HOST → Hostname
sudo systemctl daemon-reload && sudo systemctl enable --now aicampus-compute-tunnel

# Test: Agent hinter dem Tunnel
curl -s http://127.0.0.1:8701/health
```

Mehrere Compute-Server: eine Tunnel-Unit pro Server (lokale Ports 8701,
8702, …).

### 5.3 Registry in der Admin-Konsole

Admin → Systemeinstellungen → **Compute/Workspace**:

```
Name: local       URL: http://127.0.0.1:8700   [🧪 Test]
Name: compute-1   URL: http://127.0.0.1:8701   Key: …     [🧪 Test]
Name: compute-2   URL: http://127.0.0.1:8702   Key: …     [🧪 Test]
```

Je Engine wird der GPU-Zugang in der Engine-UI gewählt (Checkboxen für die
von der Engine gemeldeten GPUs; Default alle, „keine" = nur CPU).
Speichern ist automatisch (Auto-Save). Der Statusbereich zeigt die Health
aller Engines (30-s-Cache).

### 5.4 Betriebsprüfung

- **Tunnel down?** Agent im Status rot; Workspace-Aufgaben bleiben sichtbar,
  „Ausführen/Abgeben" ausgegraut („⚠️ Compute-Server nicht erreichbar").
  Der Tunnel stellt sich selbst wieder her (`Restart=always` +
  `ServerAliveInterval`), AICampus braucht nicht neu gestartet zu werden.
- **Agent neugestartet?** Lauffähige Workspaces (Volumes) bleiben erhalten;
  laufende Jobs sind nach dem Neustart „killed" (UI zeigt das an).
- **Image-Spec geändert?** Neuer Content-Hash → neues Tag → beim nächsten
  Installieren werden nur die geänderten Schichten neu gebaut.

## 6. LLM hinter SSH erreichen (LLM-Tunnel-Service)

Wenn der LLM-Server in einem anderen Netz liegt oder per Firewall nicht
direkt erreichbar ist (z. B. vLLM auf einem Uni-GPU-Server, nur SSH
offen), kann man einen SSH-Local-Port-Forward als **systemd-Service**
einrichten — reboot-fest mit Auto-Restart:

```bash
# 1) SSH-Key erzeugen (falls noch nicht vorhanden) und Public-Key auf den LLM-Server kopieren
ssh-keygen -t ed25519 -C "aicampus-llm-tunnel"
ssh-copy-id user@llm-server   # alternativ: Public-Key manuell nach ~/.ssh/authorized_keys

# 2) systemd-Service anlegen (er setzt Key-Auth voraus, kein Passwort-Prompt)
sudo tee /etc/systemd/system/aicampus-llm-tunnel.service > /dev/null << 'EOF'
[Unit]
Description=AICampus LLM SSH-Tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wandel
ExecStart=/usr/bin/ssh -N -o BatchMode=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 0.0.0.0:8001:localhost:8001 user@llm-server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 3) Aktivieren
sudo systemctl daemon-reload
sudo systemctl enable --now aicampus-llm-tunnel

# 4) Prüfen: Port muss lauschen
ss -tln | grep 8001
```

Dann im `.env` einfach den lokalen Tunnel-Port als Endpoint angeben:

```bash
LLM_API_URL=http://localhost:8001/v1
```

**Wichtig bei Docker-Deployment** (AICampus läuft in einem Container):

1. Der Container erreicht den Host nicht über `localhost`, sondern über
   die Docker-Host-IP. Am einfachsten per `extra_hosts` in der
   Compose-Datei (steht bereits in `deploy/compose.local.yml`):
   ```yaml
   services:
     aicampus:
       extra_hosts: ["host.docker.internal:host-gateway"]
   ```
   und `LLM_API_URL=http://host.docker.internal:8001/v1` im `.env`.
   **Wichtig:** Der Tunnel muss an die **Bridge-IP** binden
   (`-L 172.18.0.1:8001:localhost:8001`), damit er aus dem Compose-Netz
   erreichbar ist (Standard-Netz `172.18.0.0/16`, in
   `docker network inspect` prüfen).
2. Falls auf dem Host **UFW** aktiv ist: Docker-Container-Traffic auf
   nicht-published Ports wird per Default gedroppt. Freigabe für die
   Docker-Subnetze:
   ```bash
   sudo ufw allow from 172.17.0.0/16 to any port 8001 proto tcp
   sudo ufw allow from 172.18.0.0/16 to any port 8001 proto tcp
   ```

**Fehlersuche:** `journalctl -u aicampus-llm-tunnel -n 50` — ein
`Permission denied (publickey)`-Fehler deutet auf ein Key-Problem (z. B.
überschriebene `authorized_keys` auf dem LLM-Server), sonst liegt es am
SSH-Server/Netz. In der AICampus-Admin-Konsole lässt sich die Verbindung
jederzeit per „LLM testen" prüfen.

## 7. Backups & Datenorte

| Daten | Ort (nativ) | Ort (Docker-Hybrid) |
|---|---|---|
| DB + Uploads + Task-Dateien + Snapshots | `<repo>/data/` | `<repo>/data/` (Volume) |
| Assets (Daten, Tests, Task-Skripte) | `/opt/aicampus/assets/` | `<repo>/data/compute-assets/` |
| Task-Images (init.sh-Builds) | Docker-Images `aicampus/task/*` | Docker-Images (Host) |
| Student-Volumes | Docker-Volumes `aicampus-ws-*` | Docker-Volumes (Host) |

> **Backup-Praxis:** `data/` sichern (konsistent stoppen oder
> `sqlite3 data/aicampus.db ".backup …"`); Docker-Volumes/Images der
> laufenden Workspaces zusätzlich, falls Fortschritt erhalten bleiben soll.

## 8. Sicherheit (Kurzfassung)

- Agent bindet nur auf `127.0.0.1` (nativ) bzw. nur im Compose-Netz
  (Docker). Einziger externer Zugangsweg: SSH-Tunnel mit Key-Auth.
- Jede AICampus→Agent-Request trägt ein HMAC-Op-Token (60 s, scopet auf
  Workspace/Task). Key leer = offen (NUR Entwicklung!).
- Workspace-Container: read-only Root-FS, `--network=none` (Default;
  `internet: true` in der Spec = opt-in Bridge), CPU/RAM/PID-Limits,
  einziger schreibbarer Ort `/workspace`.
- Private Dateien (`.solution/`, `.tests/`) liegen nur auf der
  AICampus-Disk und werden ausschließlich beim Grading in einen frischen
  Container injiziert — nie ins Student-Volume.
- **In Produktion:** `SECRET_KEY` setzen (JWT), Admin-Passwort ändern,
  `DEBUG=false`, nginx mit TLS davor.
