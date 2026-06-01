"""Endpoint genérico de exportação: recebe o que está na tela e devolve CSV/XLSX/PDF."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from auth import require_admin
import exporters

router = APIRouter()


class ExportBody(BaseModel):
    format:  str                 # csv | xlsx | pdf
    title:   str = "Extrato"
    headers: list[str]
    rows:    list[list]          # já formatadas como na tela
    filename: str = "export"


@router.post("/api/export")
def exportar(body: ExportBody, request: Request, admin: str = Depends(require_admin)):
    try:
        conteudo, media_type, ext = exporters.build(body.format, body.headers, body.rows, body.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Falha ao gerar {body.format}: {e}")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", body.filename).strip("-") or "export"
    return Response(content=conteudo, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{safe}.{ext}"'})
