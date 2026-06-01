"""Rotas da UI admin para o Gerador de Orçamentos, Fornecedores e Catálogo.

As páginas chegam autenticadas (cookie de sessão admin via require_admin).
Os endpoints de dados (/api/sheets/*, /api/fornecedores/*, /api/produtos/*,
/api/orcamento/*) já existem no backend e são consumidos via fetch pelo JS
embutido nos templates.
"""
from fastapi import APIRouter, Depends, Request

from auth import require_admin
from templates_config import templates

router = APIRouter(prefix="/admin", tags=["admin-ferramentas"])


@router.get("/orcamento")
def orcamento_gerador(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/orcamento_gerador.html", {"admin": admin})


@router.get("/fornecedores")
def fornecedores(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/fornecedores.html", {"admin": admin})


@router.get("/catalogo")
def catalogo(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/catalogo.html", {"admin": admin})


@router.get("/conciliacao")
def conciliacao(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/conciliacao.html", {"admin": admin})


@router.get("/conciliacao/itau")
def conciliacao_itau(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/conciliacao_itau.html", {"admin": admin})
