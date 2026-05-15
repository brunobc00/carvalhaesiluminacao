from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from database import get_db
from models import Produto
from templates_config import templates

router = APIRouter(tags=["produtos"])


@router.get("/produtos")
def lista_produtos(
    request: Request,
    categoria: str = None,
    db: Session = Depends(get_db)
):
    query = db.query(Produto).filter(Produto.ativo == True)
    if categoria:
        query = query.filter(Produto.categoria == categoria)
    query = query.order_by(Produto.ordem, Produto.id)
    produtos = query.all()

    # Categorias disponíveis para filtro
    categorias = db.query(Produto.categoria).filter(
        Produto.ativo == True,
        Produto.categoria != None
    ).distinct().all()
    categorias = [c[0] for c in categorias if c[0]]

    return templates.TemplateResponse(request, "produtos.html", {
        "produtos": produtos,
        "categorias": categorias,
        "categoria_ativa": categoria,
    })


@router.get("/produtos/{slug}")
def detalhe_produto(slug: str, request: Request, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    produto = db.query(Produto).filter(
        Produto.slug == slug,
        Produto.ativo == True
    ).first()
    if not produto:
        raise HTTPException(status_code=404, detail="Produto nao encontrado")
    return templates.TemplateResponse(request, "produto_detalhe.html", {
        "produto": produto,
    })
