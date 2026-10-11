#!/usr/bin/env bash
# Falla si hay slugs de modelo hardcodeados fuera de migraciones, seeds y tests.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
API="${ROOT}/api"
fail=0

ALLOW_GLOBS=(
  "!**/alembic/**"
  "!**/migrations/**"
  "!**/tests/**"
  "!**/*_test.py"
  "!**/test_*.py"
  "!**/.cursor/**"
)

echo "Comprobando slugs gemini-* y *:free hardcodeados en api/ (excl. migraciones y tests)..."

if [[ ! -d "$API" ]]; then
  echo "SKIP: no existe $API aún (Fase 0 pendiente)"
  exit 0
fi

search_py() {
  local pattern="$1"
  local label="$2"
  if rg -n "$pattern" "$API" \
    -g '*.py' \
    "${ALLOW_GLOBS[@]}" 2>/dev/null | head -20; then
    echo "FAIL: posible $label fuera de zona permitida (revisar; usar model_catalog)"
    fail=1
  fi
}

# Listas tipo GEMINI_FALLBACK en router/providers de producción
search_py 'gemini-[0-9]' 'slug gemini'
search_py ':free' 'modelo OpenRouter :free'
search_py 'GEMINI_FALLBACK|OPENROUTER_FALLBACK|DEFAULT_.*_MODEL\s*=' 'constante de cadena de modelos'

# Patrones de clave en api (doble chequeo con no-secret-leak)
if rg -n 'sk-or-v1-|AIza[0-9A-Za-z_-]{10,}|AQ\.[A-Za-z0-9]' "$API" \
  -g '*.py' "${ALLOW_GLOBS[@]}" 2>/dev/null | head -5; then
  echo "FAIL: posible clave API literal en api/"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "OK: sin slugs de modelo obvios fuera de migraciones/tests (o providers aún no presentes)"
