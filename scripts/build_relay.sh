#!/usr/bin/env sh
# Baut das Relay-Binary nativ (für einen nativ laufenden Agent;
# im Compose-Setup baut der compute_agent/Dockerfile das Binary
# selbst in einer Multi-Stage-Build-Stufe).
#
# Voraussetzungen: Go-Toolchain (>= 1.21).
# Ergebnis: compute_agent/relay/relay (static, CGO aus).
set -e
cd "$(dirname "$0")/../compute_agent/relay"
CGO_ENABLED=0 go build -ldflags="-s -w" -o relay .
echo "Bereit: $(pwd)/relay"
