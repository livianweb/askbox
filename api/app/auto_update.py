"""Actualización diaria del stack askbox en esta instancia (git pull + docker compose).

Solo opera en `ASKBOX_REPO_PATH` si existe `docker-compose.yml` con servicios api/worker.
Nunca hace force-push ni `compose down`; pull siempre `--ff-only`.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any

from app import deployops, gitops

log = logging.getLogger(__name__)

_COMPOSE_NAME = "docker-compose.yml"
_SECRET_RE = re.compile(r"(?i)(token|secret|password|bearer|api[_-]?key)=[^\s\"']+")


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_auto_update_enabled() -> bool:
    return _bool_env("ASKBOX_AUTO_UPDATE", True)


def cron_hour_utc() -> int:
    try:
        return max(0, min(23, int(os.getenv("ASKBOX_AUTO_UPDATE_HOUR", "5") or 5)))
    except ValueError:
        return 5


def cron_minute_utc() -> int:
    try:
        return max(0, min(59, int(os.getenv("ASKBOX_AUTO_UPDATE_MINUTE", "0") or 0)))
    except ValueError:
        return 0


def effective_enabled(repo_path: str) -> bool:
    if not env_auto_update_enabled():
        return False
    prefs = deployops.load_preferences(repo_path) if repo_path else {}
    return bool(prefs.get("auto_update_enabled", True))


def is_askbox_compose(repo_path: str) -> bool:
    compose = os.path.join(repo_path, _COMPOSE_NAME)
    if not os.path.isfile(compose):
        return False
    try:
        with open(compose, encoding="utf-8") as fh:
            text = fh.read(4096)
    except OSError:
        return False
    return "api:" in text and "worker:" in text and "web:" in text


def _sanitize_message(text: str | None, limit: int = 400) -> str:
    out = _SECRET_RE.sub("REDACTED", text or "")
    out = gitops.redact(out)
    return out.strip()[:limit]


def _run_git(repo_path: str, *args: str, timeout: float = 120.0) -> tuple[str | None, str | None]:
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)[:200]
    if res.returncode != 0:
        return None, _sanitize_message(res.stderr or res.stdout)
    return (res.stdout or "").strip(), None


def _resolve_remote_branch(repo_path: str) -> tuple[str, str, str | None]:
    prefs = gitops.load_preferences(repo_path)
    remote = (prefs.get("remote_name") or "origin").strip() or "origin"
    branch, err = _run_git(repo_path, "branch", "--show-current", timeout=30)
    if err:
        return remote, "", err
    if not branch:
        return remote, "", "no_branch"
    return remote, branch, None


def check_updates(repo_path: str) -> dict[str, Any]:
    """git fetch y comparar HEAD con origin/<branch>. No modifica el worktree."""
    out: dict[str, Any] = {
        "ok": False,
        "remote": None,
        "branch": None,
        "local_head": None,
        "remote_head": None,
        "behind": 0,
        "dirty": False,
        "error": None,
    }
    if not gitops.is_repo_available(repo_path):
        out["error"] = "repo_not_available"
        return out
    remote, branch, err = _resolve_remote_branch(repo_path)
    if err:
        out["error"] = err
        return out
    if not branch:
        out["error"] = "no_branch"
        return out
    out["remote"] = remote
    out["branch"] = branch
    dirty_raw, _ = _run_git(repo_path, "status", "--porcelain", "-uno", timeout=30)
    out["dirty"] = bool(dirty_raw)
    _, fetch_err = _run_git(repo_path, "fetch", remote, "--prune", timeout=180)
    if fetch_err:
        out["error"] = f"fetch_failed: {fetch_err}"
        return out
    local, _ = _run_git(repo_path, "rev-parse", "HEAD", timeout=15)
    remote_ref = f"{remote}/{branch}"
    remote_head, rb_err = _run_git(repo_path, "rev-parse", remote_ref, timeout=15)
    if rb_err:
        out["error"] = f"remote_ref_missing: {rb_err}"
        return out
    out["local_head"] = (local or "")[:12]
    out["remote_head"] = (remote_head or "")[:12]
    behind_raw, _ = _run_git(repo_path, "rev-list", "--count", f"HEAD..{remote_ref}", timeout=30)
    try:
        out["behind"] = int(behind_raw or "0")
    except ValueError:
        out["behind"] = 0
    out["ok"] = True
    return out


def docker_compose_up(repo_path: str, tunnel_enabled: bool, dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        return True, "dry_run: omitido docker compose up"
    if not shutil.which("docker"):
        return False, "docker no encontrado en PATH"
    from app.tunnel_apply import docker_socket_available

    if not docker_socket_available():
        return False, "docker.sock no disponible (monta /var/run/docker.sock en el worker)"
    cmd = ["docker", "compose"]
    if tunnel_enabled:
        cmd += ["--profile", "tunnel"]
    cmd += ["up", "-d", "--build"]
    try:
        res = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, _sanitize_message(str(exc))
    if res.returncode != 0:
        return False, _sanitize_message(res.stderr or res.stdout or "compose failed")
    return True, "docker compose up -d --build OK"


def run_auto_update(repo_path: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Ejecuta el ciclo completo o dry-run. Persiste resultado en deploy-settings."""
    path = (repo_path or os.getenv("ASKBOX_REPO_PATH") or "").strip() or gitops.default_repo_path()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result: dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "skip_reason": None,
        "dry_run": dry_run,
        "started_at": started,
        "finished_at": None,
        "updates_available": False,
        "pulled": False,
        "rebuilt": False,
        "message": "",
        "check": None,
    }

    if not effective_enabled(path):
        result["skipped"] = True
        result["skip_reason"] = "disabled"
        result["message"] = "Auto-update desactivado (ASKBOX_AUTO_UPDATE o preferencias)."
        result["ok"] = True
        _persist(path, result)
        return result

    if not gitops.is_repo_available(path):
        result["message"] = "Repo no accesible desde este proceso."
        result["skip_reason"] = "repo_unavailable"
        result["skipped"] = True
        _persist(path, result)
        return result

    if not is_askbox_compose(path):
        result["message"] = f"No es un stack askbox válido (falta {_COMPOSE_NAME} con api/worker/web)."
        result["skip_reason"] = "invalid_stack"
        result["skipped"] = True
        _persist(path, result)
        return result

    check = check_updates(path)
    result["check"] = check
    if not check.get("ok"):
        result["message"] = check.get("error") or "check_failed"
        _persist(path, result)
        log.warning("auto-update check falló: %s", result["message"])
        return result

    if check.get("dirty"):
        result["skipped"] = True
        result["skip_reason"] = "dirty_worktree"
        result["message"] = "Worktree con cambios locales; abortado (no pull)."
        result["ok"] = False
        _persist(path, result)
        log.warning("auto-update: worktree sucio, abortando")
        return result

    behind = int(check.get("behind") or 0)
    result["updates_available"] = behind > 0
    if behind == 0:
        result["ok"] = True
        result["skipped"] = True
        result["skip_reason"] = "up_to_date"
        result["message"] = f"Ya actualizado ({check.get('branch')} @ {check.get('local_head')})."
        _persist(path, result)
        log.info("auto-update: sin commits nuevos")
        return result

    remote = check["remote"]
    branch = check["branch"]
    log.info(
        "auto-update: %s commits detrás de %s/%s (dry_run=%s)",
        behind,
        remote,
        branch,
        dry_run,
    )

    if dry_run:
        result["ok"] = True
        result["message"] = (
            f"dry-run: habría hecho pull --ff-only ({behind} commits) y docker compose up -d --build."
        )
        _persist(path, result)
        return result

    _, pull_err = _run_git(path, "pull", "--ff-only", remote, branch, timeout=300)
    if pull_err:
        result["message"] = f"pull falló (¿divergencia?): {pull_err}"
        _persist(path, result)
        log.error("auto-update pull falló: %s", pull_err)
        return result
    result["pulled"] = True

    prefs = deployops.load_preferences(path)
    tunnel_enabled = bool(prefs.get("tunnel_enabled"))
    rebuilt, compose_msg = docker_compose_up(path, tunnel_enabled, dry_run=False)
    result["rebuilt"] = rebuilt
    if not rebuilt:
        result["message"] = f"pull OK pero compose falló: {compose_msg}"
        _persist(path, result)
        log.error("auto-update compose: %s", compose_msg)
        return result

    result["ok"] = True
    result["message"] = f"Actualizado desde {remote}/{branch} ({behind} commits). {compose_msg}"
    _persist(path, result)
    log.info("auto-update completado: %s", result["message"])
    return result


def _persist(repo_path: str, result: dict[str, Any]) -> None:
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not repo_path or not gitops.is_repo_available(repo_path):
        return
    try:
        deployops.record_auto_update_run(repo_path, result)
    except OSError as exc:
        log.warning("no se pudo guardar estado auto-update: %s", exc)


def status_snapshot(repo_path: str) -> dict[str, Any]:
    prefs = deployops.load_preferences(repo_path) if gitops.is_repo_available(repo_path) else {}
    return {
        "env_enabled": env_auto_update_enabled(),
        "preference_enabled": bool(prefs.get("auto_update_enabled", True)),
        "effective_enabled": effective_enabled(repo_path) if gitops.is_repo_available(repo_path) else env_auto_update_enabled(),
        "schedule_utc": f"{cron_hour_utc():02d}:{cron_minute_utc():02d}",
        "last_run_at": prefs.get("auto_update_last_run_at"),
        "last_status": prefs.get("auto_update_last_status"),
        "last_message": prefs.get("auto_update_last_message"),
        "last_dry_run": prefs.get("auto_update_last_dry_run"),
        "docker_socket": shutil.which("docker") is not None,
    }
