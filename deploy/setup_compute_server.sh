#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────
# AICampus Compute-Server: Setup-Skript (AUF DEM COMPUTE-SERVER ausführen)
#
# Installiert: Docker (falls fehlt), nvidia-container-toolkit (falls GPU),
# User/Dienste/Verzeichnisse, Agent-Venv, installiert die systemd-Unit.
#
# Hinweis: Workspace-Images werden NICHT vom Skript gebaut — die Engine
# bekommt sie über die AICampus-UI (Admin/Kurs → Compute-Engine →
# „＋ Image installieren“ aus den Image-Specs).
#
# Aufruf (als root):
#   ./deploy/setup_compute_server.sh /pfad/zum/AICampus-Repo
#
# Danach:
#   1. AGENT_KEY in /etc/aicampus/compute-agent.env setzen (= COMPUTE_AGENT_KEY
#      in der AICampus-.env)
#   2. SSH-Zugang für den Tunnel einrichten (s. deploy/aicampus-compute-tunnel.service)
#   3. In der AICampus-Admin-Konsole (Compute/Workspace) die Engine registrieren:
#      Name z. B. "compute-1", URL "http://127.0.0.1:8701" (Tunnel-Port!),
#      Key, GPU-Zugang (welche GPUs erlaubt sind)
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="${1:?Usage: $0 <pfad-zum-AICampus-Repo> (muss compute_agent/ enthalten)}"
REPO="$(cd "$REPO" && pwd)"
AGENT_USER="aicampus"
INSTALL_DIR="/opt/aicampus"

have() { command -v "$1" >/dev/null 2>&1; }
apt_get() { apt-get -y "$@"; }

echo "=== AICampus Compute-Server Setup ==="
echo "Repo: $REPO"
echo

# ── 0. Checks ─────────────────────────────────────────────────────
[[ -f "$REPO/compute_agent/main.py" ]] || { echo "FEHLT: $REPO/compute_agent/main.py"; exit 1; }

# ── 1. Docker ─────────────────────────────────────────────────────
if ! have docker; then
  echo "[1] Docker installieren ..."
  apt_get update
  apt_get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
http://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt_get update
  apt_get install -y docker-ce docker-ce-cli containerd.io
  systemctl enable --now docker
else
  echo "[1] Docker vorhanden ✓"
fi

# ── 2. NVIDIA-Container-Toolkit (nur mit GPU) ─────────────────────
if have nvidia-smi; then
  if ! docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L >/dev/null 2>&1; then
    echo "[2] nvidia-container-toolkit installieren ..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt_get update
    apt_get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
  fi
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L \
    && echo "[2] GPU-Passthrough ✓"
else
  echo "[2] Keine GPU (nvidia-smi fehlt) — CPU-Betrieb"
fi

# ── 3. User + Verzeichnisse ───────────────────────────────────────
echo "[3] User + Verzeichnisse ..."
id "$AGENT_USER" &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin "$AGENT_USER"
mkdir -p "$INSTALL_DIR/assets" /etc/aicampus
chown -R "$AGENT_USER":"$AGENT_USER" "$INSTALL_DIR"

# Repo hinlegen (kopieren, damit Updates per Re-Kopieren/Skript laufen)
if [[ "$REPO" != "$INSTALL_DIR/repo" ]]; then
  mkdir -p "$INSTALL_DIR/repo"
  cp -a "$REPO/compute_agent" "$INSTALL_DIR/repo/"
fi
chown -R "$AGENT_USER":"$AGENT_USER" "$INSTALL_DIR/repo"

# ── 4. Python-Venv für den Agenten ────────────────────────────────
echo "[4] Venv + Abhängigkeiten ..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/repo/compute_agent/requirements.txt"

# ── 5. Agent-Env + systemd-Unit ───────────────────────────────────
echo "[5] Agent-Service installieren ..."
[[ -f /etc/aicampus/compute-agent.env ]] || cp "$REPO/deploy/compute-agent.env.example" /etc/aicampus/compute-agent.env
chown root:"$AGENT_USER" /etc/aicampus/compute-agent.env
chmod 640 /etc/aicampus/compute-agent.env
cp "$REPO/deploy/aicampus-compute-agent.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable aicampus-compute-agent
echo "  → Agent läuft NACH NEU-SETZEN DES AGENT_KEY (siehe unten) — noch nicht gestartet."

echo
echo "=== Setup fertig — noch zu tun ==="
echo
echo "  1. AGENT_KEY setzen (== COMPUTE_AGENT_KEY in der AICampus-.env):"
echo "       sudo nano /etc/aicampus/compute-agent.env"
echo "  2. Agent starten:"
echo "       sudo systemctl start aicampus-compute-agent"
echo "       sudo systemctl status aicampus-compute-agent   # + journalctl -u aicampus-compute-agent -f"
echo "  3. Schnelltest (auf diesem Server):"
echo "       curl -s http://127.0.0.1:8700/health    # → {\"ok\": true, ...}"
echo "  4. SSH-Tunnel auf dem AICAMPUS-Server einrichten:"
echo "       s. deploy/aicampus-compute-tunnel.service"
echo "  5. Engine in der AICampus-Admin-Konsole registrieren:"
echo "       Admin → Systemeinstellungen → Compute/Workspace → „＋ Engine hinzufügen“"
echo "       URL = http://127.0.0.1:8701 (der TUNNEL-Port auf dem AICampus-Server)"
echo "  6. Images auf der Engine installieren (einmalig je Image-Spec):"
echo "       Admin/Kurs → Compute-Engine → „＋ Image installieren“"
echo
