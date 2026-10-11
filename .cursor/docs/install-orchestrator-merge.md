# Fusión al instalar cursor-agent-orchestrator

Cuando `install.sh` del kit encuentra el **mismo archivo** con contenido distinto en el proyecto
destino, no hace falta elegir solo entre skip y `--force`.

## Flags

| Flag | Efecto |
|------|--------|
| (ninguno) | Skip + aviso |
| `--merge` | Askbox propone fusión en `*.merge-proposal`; informe en `.cursor/install-merge-policy-report.md` |
| `--merge-apply` | Igual, pero aplica la fusión con backup `.bak-*` |
| `--force` | Copia del kit con backup (sin askbox) |

## Capa A (askbox)

Requiere `ASKBOX_API_URL` y `ASKBOX_ACCESS_TOKEN` en el entorno donde ejecutas `install.sh`.
Sin ellas, el script **avisa** y deja propuestas side-by-side para resolver con Cursor (Auto).

Implementación en el kit: `scripts/install-merge-askbox.py` → `POST /v1/ask`, tier `balanced`.
