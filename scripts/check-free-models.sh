#!/usr/bin/env bash
# Compara modelos free publicados con model_catalog y opcionalmente sondea la API.
# Uso (desde la raíz del repo):
#   ./scripts/check-free-models.sh
#   ./scripts/check-free-models.sh --json
#   ./scripts/check-free-models.sh --no-probe
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec docker compose exec -T api python -m app.cli catalog-sync-check "$@"
