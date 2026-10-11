from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.deps import AskAuth, ask_auth, current_user, err
from app.project_auth import has_scope
from app.routers.projects import get_project

router = APIRouter(prefix="/v1", tags=["usage"])


@router.get("/projects/{project_id}/usage")
async def project_usage(
    project_id: str,
    request: Request,
    days: int = 30,
    auth: AskAuth = Depends(ask_auth),
):
    days = max(1, min(days, 366))
    pool = request.app.state.pool
    if auth.mode == "access_token":
        if str(auth.project_id) != project_id:
            raise err(403, "forbidden", "El token no pertenece a este proyecto.")
        if not has_scope(auth.scopes, "usage:read"):
            raise err(403, "forbidden", "El token no tiene scope usage:read.")
        project = await pool.fetchrow("SELECT * FROM projects WHERE id = $1", auth.project_id)
    else:
        project = await get_project(pool, auth.user_id, project_id)

    rows = await pool.fetch(
        "SELECT day, requests_ok, requests_error, cache_hits, input_tokens, output_tokens, estimated_cost_usd "
        "FROM usage_daily WHERE project_id = $1 AND day >= (current_date - $2::int) ORDER BY day DESC",
        project["id"],
        days - 1,
    )
    totals = {
        "requests_ok": 0,
        "requests_error": 0,
        "cache_hits": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    daily = []
    for r in rows:
        daily.append(
            {
                "day": r["day"].isoformat(),
                "requests_ok": r["requests_ok"],
                "requests_error": r["requests_error"],
                "cache_hits": r["cache_hits"],
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
            }
        )
        totals["requests_ok"] += r["requests_ok"]
        totals["requests_error"] += r["requests_error"]
        totals["cache_hits"] += r["cache_hits"]
        totals["input_tokens"] += int(r["input_tokens"])
        totals["output_tokens"] += int(r["output_tokens"])
        totals["estimated_cost_usd"] += float(r["estimated_cost_usd"] or 0)

    return {
        "project_id": str(project["id"]),
        "days": days,
        "totals": totals,
        "daily": daily,
    }
