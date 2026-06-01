"""
Shim de dependências para os módulos de orçamento portados do com.automacaobbc.ia.

No ia esses módulos dependiam de `deps.py` (sessão JWT + Cloudflare + controle de
páginas). Aqui o site inteiro já está atrás do Cloudflare Zero Trust e usa o login
admin por senha (auth.require_admin). Este módulo reexpõe os mesmos nomes que os
routers portados importam, mapeando tudo para o modelo de auth deste app.
"""
import os
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from auth import require_admin, COOKIE_NAME, verify_session_cookie
from database import get_session
from models import GoogleToken

# Caminhos / Ollama (Ollama roda no host via host.docker.internal — só usado no
# fallback de visão para PDFs escaneados / imagens).
WORKSPACE    = Path(os.getenv("WORKSPACE_PATH", "/app"))
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434/api/generate")
OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


def require_page(page: str):
    """No ia exigia permissão por página; aqui basta ser admin (Zero Trust + senha)."""
    def _check(admin: str = Depends(require_admin)) -> dict:
        return {"email": _connected_google_email() or "", "name": admin, "paginas": ["*"]}
    return _check


def _get_session(request: Request) -> dict | None:
    """Retorna um 'session dict' compatível se o cookie admin for válido, senão None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        username = verify_session_cookie(token)
    except HTTPException:
        return None
    return {"email": _connected_google_email() or "", "name": username, "paginas": ["*"]}


def require_session(admin: str = Depends(require_admin)) -> dict:
    """Equivalente ao require_session do ia: exige admin e devolve um session dict."""
    return {"email": _connected_google_email() or "", "name": admin, "paginas": ["*"]}


def _is_admin(session: dict) -> bool:
    return "*" in (session or {}).get("paginas", [])


def _connected_google_email() -> str | None:
    """E-mail da conta Google conectada mais recentemente (para o gerador via Sheets)."""
    with get_session() as s:
        tok = s.query(GoogleToken).order_by(GoogleToken.updated_at.desc()).first()
        return tok.email if tok else None


def _get_google_access_token(email: str | None = None) -> str:
    """Retorna o access_token Google armazenado. Como só os 2 admins usam, ignora o
    `email` e devolve o token conectado mais recente. Lança 401 se não houver."""
    with get_session() as s:
        q = s.query(GoogleToken)
        tok = (q.filter_by(email=email.lower()).first() if email else None) \
            or q.order_by(GoogleToken.updated_at.desc()).first()
    if not tok or not tok.access_token:
        raise HTTPException(
            status_code=401,
            detail="Conecte-se ao Google para usar o modo Link (Planilha).",
        )
    return tok.access_token


def salvar_google_token(email: str, access_token: str, refresh_token: str | None) -> None:
    with get_session() as s:
        tok = s.query(GoogleToken).filter_by(email=email.lower()).first()
        if tok:
            tok.access_token = access_token
            if refresh_token:
                tok.refresh_token = refresh_token
        else:
            s.add(GoogleToken(email=email.lower(), access_token=access_token, refresh_token=refresh_token))
        s.commit()
