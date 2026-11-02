"""Playbooks de IDE. Públicos: no llevan secretos ni exigen sesión."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from app.ide.vscode_roo import markdown_text, playbook

router = APIRouter(prefix="/v1/ide", tags=["ide"])


@router.get("/vscode-roo")
def vscode_roo() -> dict:
    """JSON para configurar un VS Code limpio con Roo Code. Sin Bearer."""
    return playbook()


@router.get("/vscode-roo.md")
def vscode_roo_markdown() -> Response:
    """El mismo playbook en markdown (docs/ide/vscode-roo.md)."""
    return Response(content=markdown_text(), media_type="text/markdown; charset=utf-8")
