import os
from fastapi import Request, HTTPException
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

SECRET_KEY = os.getenv("SECRET_KEY", "fallback-inseguro-mudar-em-producao")
COOKIE_NAME = "admin_session"
MAX_AGE = 60 * 60 * 8  # 8 horas


def get_signer() -> TimestampSigner:
    return TimestampSigner(SECRET_KEY)


def create_session_cookie(username: str) -> str:
    signer = get_signer()
    return signer.sign(username).decode()


def verify_session_cookie(token: str) -> str:
    """Verifica o cookie e retorna o username ou lança exceção."""
    signer = get_signer()
    try:
        username = signer.unsign(token, max_age=MAX_AGE)
        return username.decode()
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="Sessão expirada")
    except BadSignature:
        raise HTTPException(status_code=401, detail="Sessão inválida")


def require_admin(request: Request) -> str:
    """Exige admin. Aceita (1) cookie de sessão assinado ou (2) o e-mail já
    autenticado pelo Cloudflare Access (header Cf-Access-Authenticated-User-Email).
    Como o domínio inteiro está atrás do Zero Trust (só e-mails autorizados),
    quem chega com esse header já é admin — não precisa digitar senha."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            return verify_session_cookie(token)
        except HTTPException:
            pass  # cookie inválido/expirado → tenta o Cloudflare
    cf_email = request.headers.get("Cf-Access-Authenticated-User-Email")
    if cf_email:
        return cf_email
    raise HTTPException(status_code=302, detail="Redirect to login",
                        headers={"Location": "/admin/login"})
