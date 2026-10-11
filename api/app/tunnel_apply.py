"""Aplicar token de túnel en .env del host y levantar cloudflared (sin loguear secretos)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def host_env_path(repo_path: str) -> str | None:
    explicit = (os.getenv("ASKBOX_HOST_ENV_FILE") or "").strip()
    if explicit:
        return explicit
    default = os.path.join(repo_path, ".env")
    return default


def env_file_writable(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if p.exists():
        try:
            return os.access(path, os.W_OK)
        except OSError:
            return False
    parent = p.parent
    return parent.exists() and os.access(parent, os.W_OK)


def merge_env_file(path: str, updates: dict[str, str]) -> None:
    lines: list[str] = []
    seen: set[str] = set()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    out: list[str] = []
    for line in lines:
        m = _ENV_LINE.match(line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    text = "\n".join(out).rstrip("\n") + "\n"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def docker_socket_available() -> bool:
    sock = (os.getenv("DOCKER_HOST") or "unix:///var/run/docker.sock").replace("unix://", "")
    return os.path.exists(sock) and shutil.which("docker") is not None


def cloudflared_running(container_name: str = "askbox-cloudflared") -> bool:
    if not docker_socket_available():
        return False
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return container_name in (res.stdout or "")


def start_cloudflared(repo_path: str) -> tuple[bool, str]:
    if not docker_socket_available():
        return False, "Docker no disponible desde este proceso (monta docker.sock o ejecuta scripts/apply-tunnel.sh en el host)."
    compose = shutil.which("docker")
    if not compose:
        return False, "Comando docker no encontrado."
    cmd = [compose, "compose", "--profile", "tunnel", "up", "-d", "cloudflared"]
    try:
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=180, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:200]
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "docker compose falló").strip()
        err = re.sub(r"(?i)(token|secret|password)=[^\s]+", "token=REDACTED", err)
        return False, err[:400]
    return True, "cloudflared iniciado con profile tunnel."


def run_apply_script(repo_path: str, payload: dict) -> tuple[bool, str]:
    script = os.getenv("ASKBOX_TUNNEL_APPLY_SCRIPT") or os.path.join(repo_path, "scripts", "apply-tunnel.sh")
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        return False, f"Script no ejecutable: {script}"
    try:
        res = subprocess.run(
            [script],
            cwd=repo_path,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:200]
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "apply-tunnel.sh falló").strip()[:400]
        err = re.sub(r"(?i)(token|secret|password)=[^\s\"]+", "REDACTED", err)
        return False, err
    return True, (res.stdout or "apply-tunnel.sh OK").strip()[:200]


def apply_local(
    repo_path: str,
    connector_token: str,
    public_url: str,
) -> dict:
    """Escribe .env (si es posible) y levanta cloudflared. Nunca incluye el token en el dict devuelto."""
    env_path = host_env_path(repo_path)
    updates = {
        "CLOUDFLARE_TUNNEL_TOKEN": connector_token,
        "ASKBOX_PUBLIC_URL": public_url.rstrip("/"),
        "ASKBOX_COOKIE_SECURE": "true",
    }
    env_written = False
    env_note = ""
    if env_path and env_file_writable(env_path):
        try:
            merge_env_file(env_path, updates)
            env_written = True
            env_note = f"Variables actualizadas en {env_path}."
        except OSError as exc:
            env_note = f"No se pudo escribir {env_path}: {exc}"
    else:
        env_note = (
            "`.env` no escribible desde la API (normal en Docker). "
            "Monta ASKBOX_HOST_ENV_FILE o ejecuta scripts/apply-tunnel.sh en el host."
        )

    docker_started = False
    docker_note = ""
    if os.getenv("ASKBOX_TUNNEL_APPLY_SCRIPT"):
        ok, docker_note = run_apply_script(
            repo_path,
            {"public_url": public_url, "connector_token": connector_token, "cookie_secure": True},
        )
        docker_started = ok and cloudflared_running()
    elif env_written and docker_socket_available():
        ok, docker_note = start_cloudflared(repo_path)
        docker_started = ok and cloudflared_running()
    elif docker_socket_available():
        ok, docker_note = start_cloudflared(repo_path)
        docker_started = ok and cloudflared_running()
    else:
        docker_note = "Ejecuta en el host: ./scripts/apply-tunnel.sh (o monta docker.sock en el servicio api)."

    return {
        "env_updated": env_written,
        "env_note": env_note,
        "docker_started": docker_started,
        "cloudflared_running": cloudflared_running(),
        "docker_note": docker_note,
    }
