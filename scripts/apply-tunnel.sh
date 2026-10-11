#!/usr/bin/env bash
# Actualiza .env en el host y levanta cloudflared (profile tunnel).
# La API puede invocarlo con JSON en stdin: {"connector_token":"...","public_url":"https://..."}
# Nunca imprime el token en stdout/stderr.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ASKBOX_HOST_ENV_FILE:-$REPO_ROOT/.env}"

read_payload() {
  if [[ -t 0 ]]; then
    return 1
  fi
  cat
}

merge_env() {
  local token="$1" url="$2"
  python3 - "$ENV_FILE" "$token" "$url" <<'PY'
import os, re, sys
path, token, url = sys.argv[1], sys.argv[2], sys.argv[3]
updates = {
    "CLOUDFLARE_TUNNEL_TOKEN": token,
    "ASKBOX_PUBLIC_URL": url.rstrip("/"),
    "ASKBOX_COOKIE_SECURE": "true",
}
line_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
lines = []
if os.path.isfile(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
out, seen = [], set()
for line in lines:
    m = line_re.match(line)
    if m and m.group(1) in updates:
        k = m.group(1)
        out.append(f"{k}={updates[k]}")
        seen.add(k)
    else:
        out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out).rstrip("\n") + "\n")
PY
}

main() {
  if payload="$(read_payload)"; then
    token="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("connector_token",""))')"
    url="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("public_url",""))')"
    if [[ -z "$token" || -z "$url" ]]; then
      echo "JSON requiere connector_token y public_url" >&2
      exit 1
    fi
    merge_env "$token" "$url"
  else
    echo "Sin stdin: asume .env ya actualizado; solo levanto cloudflared." >&2
  fi
  cd "$REPO_ROOT"
  docker compose --profile tunnel up -d cloudflared
  echo "cloudflared profile tunnel aplicado."
}

main "$@"
