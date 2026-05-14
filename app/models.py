from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Numeric, Boolean, DateTime
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
