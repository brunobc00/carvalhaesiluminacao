import os
import uuid
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from slugify import slugify

from auth import require_admin, create_session_cookie, COOKIE_NAME
from database import get_db
from models import Produto, Orcamento, AdminUser
from templates_config import templates

router = APIRouter(prefix="/admin", tags=["admin"])

UPLOADS_DIR = Path("/app/uploads")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "admin/login.html", {"erro": None})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    senha: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or not bcrypt.checkpw(senha.encode(), user.senha_hash.encode()):
        return templates.TemplateResponse(request, "admin/login.html", {
            "erro": "Usuário ou senha inválidos."
        })
    token = create_session_cookie(username)
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 8)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    total_produtos = db.query(Produto).count()
    total_orcamentos = db.query(Orcamento).count()
    novos = db.query(Orcamento).filter(Orcamento.status == "novo").count()
    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "admin": admin,
        "total_produtos": total_produtos,
        "total_orcamentos": total_orcamentos,
        "novos_orcamentos": novos,
    })


# ─────────────────────────────────────────────
# Produtos — CRUD
# ─────────────────────────────────────────────

@router.get("/produtos")
def admin_lista_produtos(
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    produtos = db.query(Produto).order_by(Produto.ordem, Produto.id).all()
    return templates.TemplateResponse(request, "admin/produtos_lista.html", {
        "admin": admin,
        "produtos": produtos,
    })


@router.get("/produtos/novo")
def novo_produto_form(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/produto_form.html", {
        "admin": admin,
        "produto": None,
        "erro": None,
    })


@router.post("/produtos/novo")
async def criar_produto(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: str = Form(""),
    mostrar_preco: bool = Form(False),
    categoria: str = Form(""),
    ordem: int = Form(0),
    ativo: bool = Form(False),
    imagem: UploadFile = File(None),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    imagem_path = await _salvar_imagem(imagem)
    slug = _gerar_slug_unico(nome, db)
    preco_val = _parse_preco(preco)

    produto = Produto(
        nome=nome.strip(),
        slug=slug,
        descricao=descricao.strip(),
        preco=preco_val,
        mostrar_preco=mostrar_preco,
        categoria=categoria.strip() or None,
        imagem_path=imagem_path,
        ativo=ativo,
        ordem=ordem,
    )
    db.add(produto)
    db.commit()
    return RedirectResponse(url="/admin/produtos", status_code=303)


@router.get("/produtos/{produto_id}/editar")
def editar_produto_form(
    produto_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    produto = db.query(Produto).get(produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto nao encontrado")
    return templates.TemplateResponse(request, "admin/produto_form.html", {
        "admin": admin,
        "produto": produto,
        "erro": None,
    })


@router.post("/produtos/{produto_id}/editar")
async def atualizar_produto(
    produto_id: int,
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: str = Form(""),
    mostrar_preco: bool = Form(False),
    categoria: str = Form(""),
    ordem: int = Form(0),
    ativo: bool = Form(False),
    imagem: UploadFile = File(None),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    produto = db.query(Produto).get(produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto nao encontrado")

    nova_imagem = await _salvar_imagem(imagem)
    if nova_imagem:
        _remover_imagem(produto.imagem_path)
        produto.imagem_path = nova_imagem

    # Atualizar slug só se o nome mudou
    if produto.nome != nome.strip():
        produto.slug = _gerar_slug_unico(nome, db, excluir_id=produto_id)

    produto.nome = nome.strip()
    produto.descricao = descricao.strip()
    produto.preco = _parse_preco(preco)
    produto.mostrar_preco = mostrar_preco
    produto.categoria = categoria.strip() or None
    produto.ordem = ordem
    produto.ativo = ativo

    db.commit()
    return RedirectResponse(url="/admin/produtos", status_code=303)


@router.post("/produtos/{produto_id}/excluir")
def excluir_produto(
    produto_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    produto = db.query(Produto).get(produto_id)
    if produto:
        _remover_imagem(produto.imagem_path)
        db.delete(produto)
        db.commit()
    return RedirectResponse(url="/admin/produtos", status_code=303)


# ─────────────────────────────────────────────
# Orçamentos
# ─────────────────────────────────────────────

@router.get("/orcamentos")
def admin_lista_orcamentos(
    request: Request,
    status: str = None,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    query = db.query(Orcamento).order_by(Orcamento.criado_em.desc())
    if status:
        query = query.filter(Orcamento.status == status)
    orcamentos = query.all()
    return templates.TemplateResponse(request, "admin/orcamentos_lista.html", {
        "admin": admin,
        "orcamentos": orcamentos,
        "status_ativo": status,
    })


@router.post("/orcamentos/{orcamento_id}/status")
def atualizar_status_orcamento(
    orcamento_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin)
):
    orcamento = db.query(Orcamento).get(orcamento_id)
    if orcamento and status in ("novo", "em_analise", "respondido"):
        orcamento.status = status
        db.commit()
    return RedirectResponse(url="/admin/orcamentos", status_code=303)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

async def _salvar_imagem(imagem: Optional[UploadFile]) -> Optional[str]:
    if not imagem or not imagem.filename:
        return None
    ext = Path(imagem.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Tipo de arquivo nao permitido: {ext}")
    filename = f"{uuid.uuid4().hex}{ext}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    content = await imagem.read()
    (UPLOADS_DIR / filename).write_bytes(content)
    return filename


def _remover_imagem(imagem_path: Optional[str]):
    if imagem_path:
        try:
            (UPLOADS_DIR / imagem_path).unlink(missing_ok=True)
        except Exception:
            pass


def _gerar_slug_unico(nome: str, db: Session, excluir_id: int = None) -> str:
    base = slugify(nome)
    slug = base
    contador = 1
    while True:
        query = db.query(Produto).filter(Produto.slug == slug)
        if excluir_id:
            query = query.filter(Produto.id != excluir_id)
        if not query.first():
            return slug
        slug = f"{base}-{contador}"
        contador += 1


def _parse_preco(preco_str: str) -> Optional[float]:
    if not preco_str or not preco_str.strip():
        return None
    try:
        return float(preco_str.replace(",", "."))
    except ValueError:
        return None
