"""
Konfiguration des Compute-Agenten (per Umgebungsvariablen, s. .env / systemd-Unit).

Der Agent spricht die docker-CLI, verwaltet die Workspace-Container
und liegt zwischen TutorAI (über SSH-Tunnel oder 127.0.0.1) und dem
Docker-Daemon dieses Hosts.
"""

import os


def _bool(val: str, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ── Auth ──────────────────────────────────────────────────────────
# Gemeinsamer HMAC-Secret mit TutorAI (COMPUTE_AGENT_KEY). Leer = Auth
# deaktiviert (nur für lokale Entwicklung!).
AGENT_KEY: str = os.getenv("AGENT_KEY", "")

# ── Netzwerk ────────────────────────────────────────────────
# Nativ (systemd): bindet nur auf 127.0.0.1 — der SSH-Tunnel ist der
# einzige Zugangsweg. Containerisiert (Compose) läuft der Agent auf
# 0.0.0.0 IM CONTAINER; die Sicherheit bleibt über den Docker-Netz-
# Access (kein Port aufs Host-Netz, außer bewusst publishen).
HOST: str = os.getenv("AGENT_HOST", "127.0.0.1")
PORT: int = int(os.getenv("AGENT_PORT", "8700"))

# ── Verzeichnisse auf diesem Host ─────────────────────────────────
# /assets: geteilte public-Dateien/Datasets je Aufgabe (read-only-Mount)
ASSET_ROOT: str = os.getenv("ASSET_ROOT", "/opt/tutorai/assets")
# Wenn der Agent im Container läuft (Compose-Setup), ist ASSET_ROOT ein
# Container-Pfad, der auf dem HOST nicht existiert — der Docker-Daemon
# läuft aber auf dem Host und braucht Host-Pfade für Bind-Mounts.
# ASSET_ROOT_HOST benennt denselben Speicherort als Host-Pfad.
# Leer (Default) = nativ: ASSET_ROOT ist bereits der Host-Pfad.
ASSET_ROOT_HOST: str = os.getenv("ASSET_ROOT_HOST", "").strip()

# ── Container-Defaults ────────────────────────────────────────────
# Idle-Timeout: Container nach Inaktivität entfernen (Volume bleibt).
IDLE_TIMEOUT: int = int(os.getenv("IDLE_TIMEOUT", "1200"))  # 20 min
# Max. parallele GPU-Jobs auf diesem Node (FIFO-Queue via Semaphore).
GPU_MAX_JOBS: int = int(os.getenv("GPU_MAX_JOBS", "8"))
# Timeout für den Init-Build (init.sh im Build-Container) — Downloads
# großer Datasets können dauern; bei Überschreitung wird der Build
# abgebrochen und init-generierte Daten aufgeräumt.
INIT_TIMEOUT: int = int(os.getenv("AGENT_INIT_TIMEOUT", "1800"))  # 30 min
# GPU nur verwenden, wenn nvidia-smi Erfolg hat (auto) oder erzwungen (true).
GPU_ENABLED: str = os.getenv("GPU_ENABLED", "auto").strip().lower()
# Fehlende öffentliche Images beim Start nachziehen (kuratierte
# tutorai/*-Images werden NICHT gepullt — die werden gebaut, s. README).
AUTOPULL: bool = _bool(os.getenv("AGENT_AUTOPULL", "false"))

# ── Image-Liste ───────────────────────────────────────────────────
# Repos, die in der Image-Liste der Engine ausgeblendet und per API
# nicht löschbar sind (Core-Images des TutorAI-Sets: das laufende
# App-/Agent-Image soll nicht versehentlich entfernt werden).
HIDDEN_IMAGE_REPOS: frozenset[str] = frozenset(
    r.strip() for r in os.getenv(
        "HIDDEN_IMAGE_REPOS", "tutorai-app,tutorai-compute-agent"
    ).split(",") if r.strip()
)

# ── Limits (Sicherheit, s. Plan §9) ───────────────────────────────
MAX_FILE_SIZE: int = 50 * 1024 * 1024          # 50 MB pro Datei
# Fallback-Disk-Quota pro Workspace (wenn die Aufgabe keine eigene
# Quota gesetzt hat) + Cap für Snapshots (tar.gz) — mit der Task-Quota
# zusammengelegt: Snapshot-Cap = Quota (bei Überschreitung erst aufräumen).
MAX_WORKSPACE_SIZE: int = 1024 * 1024 * 1024   # 1 GB
MAX_ASSET_FILE: int = 5 * 1024 * 1024 * 1024   # 5 GB pro Asset-Datei (Datasets)
MAX_TIMEOUT: int = 7200                         # 2 h harte Obergrenze
DEFAULT_TIMEOUT: int = 900                      # 15 min
LOG_TAIL_LINES: int = 50                        # Log-Tail je Run
