from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.deps import current_user

router = APIRouter(prefix="/v1", tags=["alerts"])


@router.get("/alerts")
async def list_alerts(request: Request, user: dict = Depends(current_user)):
    rows = await request.app.state.pool.fetch(
        "SELECT k.id, k.project_id, p.name AS project_name, k.provider, k.label, k.last4, k.status, "
        "k.last_used_at, k.created_at "
        "FROM api_keys k JOIN projects p ON p.id = k.project_id "
        "WHERE p.user_id = $1 AND k.status IN ('invalid', 'exhausted') "
        "ORDER BY k.status, k.created_at DESC",
        user["id"],
    )
    return {
        "alerts": [
            {
                "type": "api_key",
                "severity": "error" if r["status"] == "invalid" else "warning",
                "key_id": str(r["id"]),
                "project_id": str(r["project_id"]),
                "project_name": r["project_name"],
                "provider": r["provider"],
                "label": r["label"] or "",
                "last4": r["last4"],
                "status": r["status"],
                "message": (
                    f"Clave {r['provider']} ···{r['last4']} marcada como inválida."
                    if r["status"] == "invalid"
                    else f"Clave {r['provider']} ···{r['last4']} agotada o en cuota."
                ),
            }
            for r in rows
        ]
    }
