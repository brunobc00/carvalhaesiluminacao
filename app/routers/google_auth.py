"""
OAuth Google — usado apenas pelo Gerador de Orçamentos (modo 'Link' com Google Sheets).

O acesso ao site já é protegido pelo Cloudflare Zero Trust (só os e-mails autorizados).
Este fluxo apenas obtém e guarda um token Google (escopo Sheets/Drive.file) para que o
gerador possa ler/escrever a planilha do orçamento. O token fica em GoogleToken.
"""
import os
import re
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from auth import require_admin
from orcamento_deps import salvar_google_token

router = APIRouter()

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", "")


@router.get("/api/auth/google")
async def auth_google(next: str = "orcamento", admin: str = Depends(require_admin)):
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Google OAuth não configurado (GOOGLE_CLIENT_ID/REDIRECT_URI).")
    params = urllib.parse.urlencode({
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.file",
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         next,
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/auth?{params}")


@router.get("/api/auth/google/callback")
async def auth_google_callback(code: str, state: str = "orcamento"):
    async with httpx.AsyncClient(timeout=15) as client:
        token_r = await client.post("https://oauth2.googleapis.com/token", data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GOOGLE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        })
        token_r.raise_for_status()
        tokens = token_r.json()

        user_r = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        user = user_r.json()

    email = user.get("email", "").lower()
    if not email:
        return RedirectResponse(url="/admin/orcamento?google=erro")

    salvar_google_token(
        email=email,
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token"),
    )

    dest = state if re.match(r"^[a-z0-9_-]+$", state or "") else "orcamento"
    return RedirectResponse(url=f"/admin/{dest}?google=ok")
