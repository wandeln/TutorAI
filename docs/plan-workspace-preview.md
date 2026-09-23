# Plan: Workspace-Preview & Terminal (Relay-Variante 2)

**Status: Umsetzung Phase 1** (Stand: 2026-09-22; seit 2026-09-23: Backend-Preview-Proxy vom Zweit-Port :8100 (raw asyncio) auf ASGI-Routes auf dem Haupt-Port migriert — Mixed-Content-Fix, s. Architektur)

## Ziel

Webservices aus dem **Student-Container** (Jupyter, TensorBoard, marimo,
Streamlit, eigene Apps) im Browser anzeigen: **Port-Autoerkennung +
Preview-Tunnel (Relay, Variante 2: `docker cp`) + Terminal-FEATURE**.

### Scope-Entscheidungen (User-finiert)

- **Keine Plattform-Buttons / Tool-Registry** (unflexibel, z. B. Marimo-
  Argument). Die Preview ist **tool-agnostisch**: alles, was im Container
  auf einem Port lauscht, erscheint in der „Ports“-Sektion.
- **Server-Start = `run.sh`** (bestehende Konvention — funktioniert ohne
  Änderung, sobald Erkennung + Relay + Proxy + UI da sind).
  **Background-Pattern** als dokumentiertes Standard-Pattern:
  ```sh
  pgrep -f tensorboard || tensorboard --logdir logs &
  ```
  → outlives den Run, kein Timeout-Kill, Run-Lane bleibt frei,
  TensorBoard überlebt mehrere Trainings. Foreground = Stop-Button, aber
  der Run-Timeout tötet den Server mit.
- **Kein Auto-Start** via Dockerfile/init.sh: init-Prozesse sterben im
  Commit; der Entry-Point wird ohnehin überschrieben (`sleep infinity`,
  bewusst für deterministisches Keep-Alive); read-only Rootfs → Jupyter
  bräuchte HOME-Verdrahtung; RAM ab t=0.
- **Terminal** ist in Scope: xterm.js (vendored) + WS-Kette
  Browser → Backend → Agent → `docker exec -it … sh`.
- **Kein `server.sh`** (verworfen — Plattform-Skripte wären unflexibel).

## Architektur

```
Browser (UI :8000, hinter HTTPS-Reverse-Proxy)
  │  1) iframe/WS → Preview-Proxy (Backend, HAUPT-Port :8000, ASGI-Routes)
  │     /preview/{task}/{port}/… — same-origin → kein Mixed Content
  │     Auth: JWT-Cookie · Header-Sanitizing (X-Frame-Options/CSP weg)
  ▼
Backend :8000 (ASGI; WS: Uvicorn-Handshake, Frame-Codec in preview_proxy.py)
  ── raw TCP + HMAC-Op-Token ──▶ Agent :8701  /ws-preview/{key}/preview/{port}/…
                                              (raw asyncio — HTTP→WS-Upgrade-Passthrough)
                                                │ docker exec -i <c> /tmp/relay <port>
                                                ▼
                                          Student-Container: Relay (Go-Static-Binary)
                                                │ dailt 127.0.0.1:<port>
                                                ▼
                                          Target-Server (Jupyter/TB/…)
```

**Terminal** (eigene Kette, echter WebSocket bis zum Agenten):

```
Browser xterm.js ── WS (Session-Cookie) ──▶ Backend-WS-Route (Task-Besitz)
   ── WS + HMAC-Token (Query) ──▶ Agent-WS-Route (FastAPI, Port 8700)
   ── docker exec -i -t sh (PTY)
```

### Warum Agent-Proxy raw asyncio, Backend-Proxy ASGI?

**Agent** (:8701) tunnelt rohes HTTP/1.1 (inkl. 101-Upgrade): Uvicorn
(h11/httptools) interceptet `Upgrade: websocket`-Requests VOR dem
ASGI-App-Call und wandelt sie in den ASGI-WebSocket-Fluss um
(`websocket`-Scope-Typ, App muss `websocket.accept` senden) — der für
den Relay-Tunnel nötige raw HTTP→101-Passthrough ist über ASGI nicht
möglich. (Implementierungs-Befund, 2026-09-22: ursprüngliche
`Mount("/ws-preview")`-Variante verworfen.)

**Backend** läuft als **ASGI-Routes auf dem Haupt-Port** (:8000;
migriert am 2026-09-23 vom Zweit-Port :8100, raw asyncio):
- **Mixed-Content-Fix**: Hinter dem HTTPS-Reverse-Proxy ist ein zweiter
  published Port nur als plain HTTP erreichbar → Browser blockiert
  iframe/WS. Same-Origin (Haupt-Port) funktioniert immer und spart
  einen zu publizierenden Port.
- **WS-Brücke**: Uvicorn erledigt Browser-Handshake + Frame-Decoding
  selbst (ASGI-WebSocket-Scope); `preview_proxy.py` brückt mit eigenem
  RFC-6455-Frame-Codec zur Agent-Leiste (von Agent unmasked,
  zu Agent masked — dort sind wir der Client).
- **Auth**: JWT-Cookie (stateless, portunabhängig) → dieselbe
  Session-Auth wie die übrige API.

## Komponenten

### 1. Relay-Binary (Go, static)

`compute_agent/relay/relay.go` (~40 Zeilen): dailt `127.0.0.1:<port>`
(Argument), pipet `stdin ↔ conn` und `conn ↔ stdout`, halbes Schließen
(`CloseWrite`) nach stdin-EOD, Exit 1 + stderr-Message bei Connect-Fehler.
WS-tauglich, weil die HTTP-Antwort (inkl. 101-Upgrade) einfach durch die
Pipes fließt.

- **Build**: Multi-Stage im `compute_agent/Dockerfile`
  (`golang:1.24-alpine`, `CGO_ENABLED=0`) → `/app/compute_agent/relay/relay`.
  Native-Installation: `scripts/build_relay.sh` (oder Preview meldet
  „Relay-Binary fehlt“).
- **Platzierung im Container**: on-demand per **`docker exec -i` +
  stdin-Stream** (`sh -c 'cat > /tmp/relay && chmod +x /tmp/relay'`,
  ~1–2 MB, erst beim ersten Preview pro Container; Cache im Agent).
  **KEIN `docker cp`**: Die Container haben read-only Rootfs, und der
  Docker-Daemon lehnt `cp`-Ziele außerhalb der Volumes ab (tmpfs /tmp →
  „container rootfs is marked read-only“); `cp` auf /workspace (Volume)
  funktioniert dagegen (Starter-Dateien/Seeds nutzen das weiter).
  Create-Args bleiben stabil → **keine Container-Recreates** durch den
  Relay.
- **`/tmp` tmpfs mit explizitem `exec`** (`rw,nosuid,exec,size=256m,
  mode=1777`): Der Docker-Daemon ergänzt bei tmpfs **standardmäßig
  `noexec`** (und `nodev`) — ohne explizites `exec` in der Spec gewinnt
  das Daemon-Default und der Relay wäre nicht ausführbar. Der Student
  hat auf `/workspace` ohnehin Exec-Rechte → kein Sicherheitsverlust.
  Die tmpfs-Spezifikation ist Teil des Mount-Hash-Labels
  (`tutorai.mounts`) → bei Spec-Wechsel werden bestehende Container
  **einmalig** neu angelegt (Volume bleibt).

### 2. Compute-Agent (compute_agent/)

- **`preview.py`** (neu, raw asyncio-Server auf `PREVIEW_PORT` 8701,
  start/stop in `main._startup`/`_shutdown`):
  - Pfade: `/{key}/preview/{port}/{target…}` (regex).
  - Auth: `X-Agent-Token` (HMAC, `op = ws:{key}`) via `auth.verify_token_raw`.
  - Je Request: Container-Running-Check (409), Relay sicherstellen,
    `docker exec -i <c> /tmp/relay <port>` (Popen, `start_new_session`,
    stderr-Drain-Thread), Upstream-Request bauen (Hop-by-Hop-Header aus,
    bei Upgrade: `Connection: upgrade`), Request-Body (Content-Length;
    Chunked → 411) streamen.
  - Antwort: Head parsen (32 KB Cap, 60 s Timeout → 504) → Head weiter
    (Connection normalisiert); **101** → bidirektionales Pumpen bis
    Close; sonst Body-Pump mit Content-Length-Zählung bzw. Chunked-
    End-Erkennung (`preview_pipe.ResponseBody`; Keep-Alive-Targets
    schließen sonst nicht — Relay wird dann SIGKILLt).
  - Relay-Connect-Fehler (Port frei) → 502 + Relay-stderr-Text.
  - `REGISTRY.touch(key)` je Request (Idle-Schutz).
- **`preview_pipe.py`** (neu, geteilt mit dem Backend-Proxy): Request-/\
  Response-Head-Parsing, Hop-by-Hop-Filter, Error-Antworten,
  `pump_pair` (bidirektionales Pumpen), `ResponseBody`/
  `_ChunkedFraming` (Body-Ende-Erkennung, Pass-through ohne Dekodieren).
- **`terminal.py`** (neu): PTY-Session um den docker-CLI-Prozess:
  `pty.openpty()` + `TIOCSWINSZ` (Default 160×40) → CLI erkennt TTY →
  Container-PTY wird mit dieser Größe angelegt und Resize wird
  automatisch nachgeführt (CLI pollt die Client-TTY). `add_reader` auf
  dem Master-Fd (nonblocking, EIO = Shell beendet) → WS-Binary-Frames;
  Input/Resize über `os.write` im Executor. **Resize (Phase 2)** =
  simples `ioctl` auf dem Master + Fit-Addon-Trigger im Frontend
  (Client schickt bereits `{"type":"resize"}`).
- **`docker_ops.py`**:
  - `/tmp`-tmpfs-Flag mit explizitem `exec` (Daemon-Default ist `noexec`;
    + tmpfs-Spec in `_mounts_hash`).
  - `ensure_relay(key)`: Relay per exec-stdin-Stream (kein `docker cp` —
    ro-Rootfs).
  - `list_workspace_ports(key)`: `docker exec … sh -c`-Scan über
    `/proc/net/tcp{,6}` (LISTEN-Zustand `0A` → Inode → Port/Hex) +
    `/proc/*/fd`-Walk (Inode → PID). **Kein procps/ss nötig**, funktioniert
    auch bei `network=none` (Loopback existiert). Ausgabe `[{port, pid}]`.
  - `kill_workspace_port(key, port)`: PID aus dem Scan → Prozessbaum-Kill
    (gleiche `kt()`-/`/proc`-Walk-Logik wie `runs._TREE_KILL`). Port frei
    → 404.
- **`main.py`**: neue Routes `GET /workspaces/{key}/ports`,
  `POST /workspaces/{key}/ports/{port}/kill`, `WS /workspaces/{key}/terminal`
  (Token per Query, `op = ws:{key}`; Keepalive-Touch alle 60 s, damit ein
  offenes Terminal den Container am Idle-Kill schützt); Preview-Server-
  Start/Stop in `startup`/`shutdown` (async-Handler).
- **`auth.py`**: `verify_token_raw(token)` (Signatur+Expiry, ohne Request)
  — von der FastAPI-Dependency und vom WS/PReview-Code geteilt.

### 3. Backend (TutorAI)

- **`services/preview_proxy.py`** (neu, ASGI-Routes auf dem Haupt-Port;
  `router = APIRouter()`, kein eigener Server, kein lifespan-Start):
  - `GET/POST/… /preview/{task_id}/{port}/{target…}` (Streaming-Route)
    + `WS /preview/{task_id}/{port}/{target…}`.
  - Auth: `access_token`-Cookie → `decode_access_token` → User aus DB
    (Executor/Threadpool, JWT ist stateless).
    Course-Zugriff + Workspace-Task-Check wie im Student-API
    (404/403/400/503).
  - Key = `ws-{course}-{task}-{<Session-User>}` → **kein
    Cross-Student-Zugriff** (der Student steht in der Session, nicht im
    Pfad).
  - Upstream: roher TCP-Connect zum Agent-Preview-Port
    (`PREVIEW_AGENT_PORT` 8701, Host aus `agent["url"]`), Request-Head
    + Body (Content-Length, 100 MB Cap; kein Content-Length → 411),
    Cookies (außer `access_token`)/Host raus, HMAC-Op-Token rein,
    `Connection: close`.
  - HTTP-Antwort: Head weiterleiten mit **Header-Sanitizing**:
    `X-Frame-Options` + `Content-Security-Policy` entfernen
    (sonst blockieren Jupyter & Co. das iframe; lokale Single-User-
    Installation, akzeptiert), Hop-by-Hop weg. Body als
    `StreamingResponse`; das rohe Chunked-Framing der Agent-Leiste
    wird dekodiert (`_ChunkedDecoder`), Starlette chunkt neu.
  - WS-Antwort: 101 erwartet; `sec-websocket-protocol` weiter; Pumpen
    in beide Richtungen (Browser-Seite dekodiert Uvicorn, Agent-
    Seite: `_FrameReader` unmasked inbound / masked outbound),
    Fragmentation-Reassembly, Ping→Pong, Close-Echo, 32 MB-Cap
    (→ Close 1009).
- **`config.py`**: `PREVIEW_AGENT_PORT` (Default **8701**, nur
  Agent-Netz, kein Host-Publish).
- **`main.py`**: `app.include_router(preview_proxy.router)`; Template
  baut root-relative iframe-URLs `/preview/{task}/{port}/` (keine
  `preview_base` im Kontext mehr).
- **`api/student.py`**:
  - `workspace/status`: `ports: [{port, pid}]` (isoliert try/except wie
    `disk`).
  - `POST /tasks/{id}/workspace/ports/{port}/kill`.
  - `WS /tasks/{id}/workspace/terminal`: Session-Auth (Cookie manuell),
    Task/Course-Check, Agent-Pick, `websockets.connect` (Query-Token) zum
    Agenten, 1:1-Pumpen (Text/Binary) in beide Richtungen.
- **`services/compute_client.py`**: `ports(key)`, `kill_port(key, port)`.

### 4. UI (Student-View, `task_solve_workspace.html`)

Zwei neue kleine Sektionen **unter dem Verzeichnis-Tree** (linke Spalte):

1. **Terminals**: Sektions-Header mit **+** Button. Jede Session =
   ein `docker exec`-Shell (xterm.js-Instanz). Klick = Session in der
   **Editor-Fläche** anzeigen (wechselbar), **×** neben der Session =
   schließen (WS zu → Shell endet).
2. **Ports**: alle lauschenden Ports aus dem Status-Polling (alle 8 s,
   `document.hidden`-Aware). Klick = **iframe** in der Editor-Fläche
   (`/preview/{task}/{port}/`, root-relativ auf dem Haupt-Port →
   same-origin), mit Header-Leiste
   (Label, absolute URL, ⟳ neu laden, ↗ neuem Tab). **×** neben dem Port =
   Prozess beenden (mit confirm) → Status-Refresh.

- Editor-Fläche wird zum View-Schalter: `editor | media` (bestehend,
  `wsUI`) | `terminal` | `preview`. `workspace.js` feuert neu
  `onViewChanged("editor"|"media")` (openFile/showMediaView/clearEditorState)
  + `refreshEditor()` (CodeMirror-Refresh nach Unhide) — die View-Logik
  selbst bleibt im Template.
- **xterm.js vendored** unter `static/vendor/xterm/`
  (`@xterm/xterm` 5.5.0 + `addon-fit` 0.10.0; `git add -f` nötig,
  `static/vendor/*.js` ist gitignore-t).
- Terminal-WS-Protokoll (Client→Backend→Agent, 1:1):
  - Binärframes = rohes Terminal-Input/Output.
  - Text = Kontrolle: `{"cols","rows"}` (Init) /
    `{"type":"resize","cols","rows"}` / `{"type":"exit","code"}`
    (Agent→Client bei Shell-Ende).

### 5. Laufzeit-Verhalten

- **Port-Erkennung**: nur im bestehenden Status-Endpoint (Agent-
  `ports`-Call), kein separater Poller; der 8-s-Status-Poll in der
  Student-UI reicht. Datei-Refresh nur bei Events (Reset/Run-Ende/
  manuell) — der Poll macht nur Banner + Ports (kein Baum-Rerender,
  damit D&D/Kontextmenü nicht gestört werden).
- **Idle-Kill**: offenes Terminal + laufende Preview + Status-Poll
  touchen die Activity → Container lebt, solange jemand hinschaut.
- **Container-Recreate** (einmalig, durch tmpfs-Flag-Wechsel im
  Mount-Hash): laufende Jobs brechen ab, Volumes/Dateien bleiben.

## Sicherheit

- Preview-Routes auf dem Haupt-Port: JWT-Cookie **Pflicht**; nur
  eigene Container (User-ID aus der Session). Kein zusätzlicher
  published Port.
- Backend→Agent: HMAC-Op-Token (`ws:{key}`) wie alle anderen Calls;
  raw TCP + `Connection: close`.
- `X-Frame-Options`/CSP werden im Proxy entfernt (iframe-Erfordernis;
  lokale Installation).
- **Bekannte Einschränkung (lokale Single-User-Setup)**: alle Previews
  teilen den Haupt-Origin (z. B. `127.0.0.1:8000`) → Site-Cookies
  (z. B. Jupyter-Session) sind pro Port, nicht pro Aufgabe getrennt.
  Absolute Asset-Pfade in der previewten App (z. B. `/static/…`)
  laden gegen den TutorAI-Origin — nur relative Pfade zuverlässig.
  Für Multi-User-Produktion später: Subdomain- oder Port-Map pro Preview.
- Relay: nur Loopback-Dial im Container, kein eigener Listener, kein
  Root. `pids-limit 256` & Co. bleiben bestehen.

## Limitationen / Follow-ups (Phase 2+)

- **Terminal-Resize** ist aktiv (Fit-Addon → `resize`-Messages → Agent-
  `ioctl` auf der PTY; docker-CLI führt die Größe in den Container nach).
- Relay-Listener-Cache (aktuell: 1 `docker exec` je Preview-Request,
  ~50–100 ms Spawn — Phase-1-akzeptabel).
- Port-Kill tötet den Prozessbaum des Socket-Besitzers; Kind-Prozesse
  mit geerbten Sockets (Seltenfall) könnten den Port weiter halten →
  dann „Port nicht (mehr) belegt“ beim nächsten Kill.
- `duplicate_task` kopiert keine Workspace-Dateien (bekannte Lücke,
  anderes Feature).

## Test-Anleitung (Task 41, Kurs 12)

1. **Deploy**: Rebuild BEIDER Container (`tutorai` + `compute-agent`).
   Hinweis: Bestehende Workspace-Container werden **einmalig neu
   angelegt** (tmpfs-Flag) — laufende Jobs brechen dabei ab.
2. **Ports/Preview**: In `run.sh` eine Zeile `python3 -m http.server 8000 &`
   (oder TensorBoard/Jupyter-Start) → ▶ Ausführen. Nach max. 8 s erscheint
   in der Sektion **Ports** der Port → klicken → Website in der
   Editor-Fläche. ⟳/↗-Buttons prüfen. **×** → confirm → Prozess tot,
   Port verschwindet (erneuertes Ausführen → Port zurück).
3. **Terminal**: Sektion **Terminals** → **+** → Shell in der Editor-
   Fläche. `ls`, `python3 -c …`, `pkill -f "http.server"` testen;
   zweites Terminal per **+** (Wechseln durch Klick); **×** schließt;
   Datei anklicken → zurück zum Editor.
4. **Regressions-Check**: Editor/Medien-View, Run/Tests, Abgabe wie
   bisher; Tree-D&D + Kontextmenü ungestört (Polling rendert nicht
   den Baum).
