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
    """Dependency que exige cookie de admin válido."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        from fastapi.responses import RedirectResponse
        # Lança redirect para login
        raise HTTPException(status_code=302, detail="Redirect to login",
                            headers={"Location": "/admin/login"})
    return verify_session_cookie(token)
