from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Orcamento
from templates_config import templates

router = APIRouter(tags=["orcamentos"])


@router.get("/orcamento")
def formulario_orcamento(request: Request):
    return templates.TemplateResponse(request, "orcamento.html", {})


@router.post("/orcamento")
def enviar_orcamento(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    telefone: str = Form(""),
    mensagem: str = Form(""),
    db: Session = Depends(get_db)
):
    orcamento = Orcamento(
        nome=nome.strip(),
        email=email.strip(),
        telefone=telefone.strip(),
        mensagem=mensagem.strip(),
        status="novo"
    )
    db.add(orcamento)
    db.commit()
    return RedirectResponse(url="/orcamento/sucesso", status_code=303)


@router.get("/orcamento/sucesso")
def orcamento_sucesso(request: Request):
    return templates.TemplateResponse(request, "orcamento_sucesso.html", {})
