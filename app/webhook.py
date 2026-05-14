import os
import hmac
import hashlib
import threading
import subprocess
import logging
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PROJECT_DIR = "/project"


def _run_deploy():
    """Executa git pull + docker compose up em background thread."""
    try:
        logger.info("Iniciando deploy automatico...")
        result = subprocess.run(
            ["bash", "/project/scripts/deploy.sh"],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            logger.info("Deploy concluido com sucesso:\n%s", result.stdout)
        else:
            logger.error("Deploy falhou (codigo %d):\n%s", result.returncode, result.stderr)
    except subprocess.TimeoutExpired:
        logger.error("Deploy timeout apos 300 segundos")
    except Exception as exc:
        logger.exception("Erro inesperado no deploy: %s", exc)


def _verify_signature(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET nao configurado — aceitando sem verificacao HMAC")
        return True
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    body = await request.body()

    # Verificar assinatura HMAC
    if not x_hub_signature_256:
        raise HTTPException(status_code=400, detail="Header X-Hub-Signature-256 ausente")

    if not _verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Assinatura invalida")

    # Só reagir a push no branch main
    if x_github_event != "push":
        return {"status": "ignorado", "evento": x_github_event}

    try:
        import json
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalido")

    ref = payload.get("ref", "")
    if ref != "refs/heads/main":
        return {"status": "ignorado", "ref": ref}

    # Disparar deploy em background
    thread = threading.Thread(target=_run_deploy, daemon=True)
    thread.start()

    return {"status": "deploy iniciado", "ref": ref}
