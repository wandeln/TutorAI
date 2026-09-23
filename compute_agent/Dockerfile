# Compute-Agent (FastAPI + docker-CLI)
#
# Der Agent spricht den Docker-Daemon des Hosts über docker.sock.
# Sicherheit: keine Host-Ports publishen (außer 127.0.0.1 für den
# Hybrid-Betrieb), kein AGENT_KEY = leer in Produktion.

# Relay-Quellcode zuerst kompilieren (Go-Stage) — das statische Binary
# (~1–2 MB) landet im Agent-Image und wird on-demand per `docker cp`
# in die Workspace-Container kopiert (Preview-Tunnel, s. plan-workspace-preview.md).
FROM golang:1.24-alpine AS relay
WORKDIR /build
COPY compute_agent/relay/go.mod compute_agent/relay/relay.go .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o /out/relay .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Container-intern: 0.0.0.0 binden (Erreichbarkeit nur über das
    # Compose-Netz / host-gebundenen Port — s. deploy/README.md)
    AGENT_HOST=0.0.0.0

# docker-CLI (spricht den Daemon des Hosts via Socket)
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY compute_agent/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY compute_agent/ compute_agent/
COPY --from=relay /out/relay compute_agent/relay/relay

EXPOSE 8700
CMD ["python", "-m", "compute_agent"]
