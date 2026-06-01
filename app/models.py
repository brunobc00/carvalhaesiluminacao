from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Boolean, DateTime, ForeignKey,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Produto(Base):
    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    slug = Column(String(220), unique=True, nullable=False, index=True)
    descricao = Column(Text, nullable=True)
    preco = Column(Numeric(10, 2), nullable=True)
    mostrar_preco = Column(Boolean, default=True)
    categoria = Column(String(100), nullable=True)
    imagem_path = Column(String(300), nullable=True)
    ativo = Column(Boolean, default=True)
    ordem = Column(Integer, default=0)
    criado_em = Column(DateTime, default=datetime.utcnow)


class Orcamento(Base):
    __tablename__ = "orcamentos"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False)
    telefone = Column(String(30), nullable=True)
    mensagem = Column(Text, nullable=True)
    status = Column(String(20), default="novo")  # novo | em_analise | respondido
    criado_em = Column(DateTime, default=datetime.utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    senha_hash = Column(String(300), nullable=False)


# ─────────────────────────────────────────────
# Orçamentos internos — Fornecedores / Tabelas de Preço / Catálogo
# (migrado de com.automacaobbc.ia — DB carvalhaes)
# ─────────────────────────────────────────────

class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    nome               = Column(String(200), nullable=False)
    nome_representante = Column(String(200))
    whatsapp           = Column(String(50))
    email_cotacao      = Column(String(200))
    email_pedido       = Column(String(200))
    contato_nome       = Column(String(200))
    contato_tel        = Column(String(50))
    contato_email      = Column(String(200))
    prazo_entrega      = Column(Integer)
    compra_minima      = Column(Numeric(12, 2))
    cond_pagamento     = Column(String(500))
    desconto_volume    = Column(Text)
    criado_em          = Column(DateTime, server_default=func.now())

    tabelas = relationship("TabelaPreco", back_populates="fornecedor", cascade="all, delete-orphan")


class TabelaPreco(Base):
    __tablename__ = "tabelas_preco"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id", ondelete="CASCADE"), nullable=False)
    data_upload   = Column(DateTime, server_default=func.now())
    arquivo_nome  = Column(String(500))
    arquivo_path  = Column(String(1000))
    arquivo_tipo  = Column(String(10))   # pdf, xls, xlsx, txt, jpg, jpeg, png
    desconto      = Column(Numeric(5, 2), default=0)   # %
    ipi           = Column(Numeric(5, 2), default=0)   # %
    icms_entrada  = Column(Numeric(5, 2), default=0)   # % — informativo
    st            = Column(Numeric(5, 2), default=0)   # %
    # aguardando | processando | processado | revisao | erro
    status        = Column(String(20), default="aguardando")

    fornecedor = relationship("Fornecedor", back_populates="tabelas")
    produtos   = relationship("ProdutoTabela", back_populates="tabela", cascade="all, delete-orphan")


class ProdutoTabela(Base):
    __tablename__ = "produtos_tabela"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    tabela_id      = Column(Integer, ForeignKey("tabelas_preco.id", ondelete="CASCADE"), nullable=False)
    codigo         = Column(String(100))
    descricao      = Column(String(1000), nullable=False)
    descricao_completa = Column(Text)
    linha_produto      = Column(String(500))  # seção/linha do fabricante no PDF
    observacao         = Column(Text)
    ncm                = Column(String(20))
    unidade        = Column(String(20))
    preco_base          = Column(Numeric(12, 4))
    preco_desconto      = Column(Numeric(12, 4))
    preco_custo         = Column(Numeric(12, 4))
    ipi                 = Column(Numeric(5, 2))
    icms_entrada        = Column(Numeric(5, 2))
    st                  = Column(Numeric(5, 2))
    descricao_generica  = Column(String(300))
    url_produto         = Column(String(500))
    imagens             = Column(Text)   # JSON array de paths/URLs

    tabela = relationship("TabelaPreco", back_populates="produtos")


class GoogleToken(Base):
    """Token OAuth Google para o Gerador de Orçamentos (modo 'Link' com Google Sheets)."""
    __tablename__ = "google_tokens"

    email         = Column(String(200), primary_key=True)
    access_token  = Column(Text, nullable=False)
    refresh_token = Column(Text)
    updated_at    = Column(DateTime, server_default=func.now(), onupdate=func.now())
