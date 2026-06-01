"""
Cliente nativo da API de Extrato Conta Corrente do Itaú (mTLS + OAuth).

Credenciais/cert da Carvalhaes vêm do ambiente (.env, base64):
  ITAU_CLIENT_ID, ITAU_CLIENT_SECRET, ITAU_ACCOUNT_ID,
  ITAU_CERT_B64 (certificado PEM em base64), ITAU_KEY_B64 (chave privada PEM em base64).

Fluxo:
  1) POST https://sts.itau.com.br/api/oauth/token (mTLS + client_credentials) -> access_token
  2) GET  https://account-statement.api.itau.com/account-statement/v1/statements/{account}
          (mTLS + Bearer) paginado -> eventos do extrato
Normaliza cada evento para o formato usado pela conciliação (ConciliacaoItau).
"""
import base64
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime

import httpx

TOKEN_URL     = "https://sts.itau.com.br/api/oauth/token"
STATEMENT_URL = "https://account-statement.api.itau.com/account-statement/v1/statements/{account}"


class ItauError(Exception):
    pass


def _cfg() -> dict:
    cid  = os.getenv("ITAU_CLIENT_ID", "")
    csec = os.getenv("ITAU_CLIENT_SECRET", "")
    acct = os.getenv("ITAU_ACCOUNT_ID", "")
    cert = os.getenv("ITAU_CERT_B64", "")
    key  = os.getenv("ITAU_KEY_B64", "")
    if not all([cid, csec, acct, cert, key]):
        raise ItauError("Integração Itaú não configurada (ITAU_CLIENT_ID/SECRET/ACCOUNT_ID/CERT_B64/KEY_B64).")
    return {"client_id": cid, "client_secret": csec, "account_id": acct, "cert": cert, "key": key}


@contextmanager
def _cert_files(cfg: dict):
    """Escreve cert/key em arquivos temporários (necessário p/ mTLS no httpx) e limpa depois."""
    cf = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    try:
        cf.write(base64.b64decode(cfg["cert"])); cf.close()
        kf.write(base64.b64decode(cfg["key"]));  kf.close()
        os.chmod(kf.name, 0o600)
        yield cf.name, kf.name
    finally:
        for p in (cf.name, kf.name):
            try:
                os.unlink(p)
            except OSError:
                pass


def _classificar(lancamento: str) -> str:
    up = (lancamento or "").upper()
    if "CIELO" in up:
        return "cielo"
    if any(k in up for k in ("REDE", "REDECARD", "SISDEB")):
        return "rede"
    return "outros"


def _parse_evento(ev: dict) -> dict | None:
    """Normaliza um evento da API para o formato de ConciliacaoItau."""
    lit = ev.get("literal") or {}
    lancamento = (lit.get("complete") or lit.get("shortened") or lit.get("complementary") or "").strip()
    cp = ev.get("counterpart") or {}
    razao = (cp.get("name") or "").strip()

    amount = (ev.get("amount") or {}).get("value")
    if amount is None:
        return None
    valor = abs(float(amount))
    # operation: 'C' (crédito) positivo, 'D' (débito) negativo — reconciliação usa valor > 0
    op = (ev.get("operation") or "").upper()
    if op.startswith("D"):
        valor = -valor

    dt = (ev.get("date") or {})
    raw = dt.get("accounting") or dt.get("event") or ""
    data = None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            data = datetime.strptime(raw[:len(fmt) + 2] if "T" in raw else raw[:10], fmt)
            break
        except (ValueError, TypeError):
            continue

    origin = ev.get("origin") or {}
    tipo = (origin.get("type") or "").strip()
    if not tipo:
        tipo = "Crédito" if not op.startswith("D") else "Débito"
    documento = (lit.get("code") or "").strip()

    return {
        "data": data,
        "lancamento": lancamento[:300],
        "razao_social": razao[:300],
        "valor": valor,
        "fonte_operadora": _classificar(lancamento),
        "tipo": tipo[:40],
        "documento": documento[:40],
    }


def fetch_eventos(date_from: str, date_to: str) -> list[dict]:
    """date_from/date_to no formato YYYY-MM-DD. Retorna lista de lançamentos normalizados."""
    cfg = _cfg()
    eventos: list[dict] = []
    with _cert_files(cfg) as (certfile, keyfile):
        with httpx.Client(cert=(certfile, keyfile), timeout=60) as client:
            tok = client.post(TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            if tok.status_code != 200:
                raise ItauError(f"Falha no OAuth Itaú ({tok.status_code}): {tok.text[:200]}")
            access = tok.json().get("access_token")
            if not access:
                raise ItauError("OAuth Itaú não retornou access_token.")

            url = STATEMENT_URL.format(account=cfg["account_id"])
            page = 1
            while True:
                r = client.get(url, headers={
                    "Authorization": f"Bearer {access}",
                    "x-itau-correlationid": str(uuid.uuid4()),
                    "Accept": "application/json",
                }, params={
                    "type": "current_account",
                    "start_date": date_from,
                    "end_date": date_to,
                    "page_size": 200,
                    "page": page,
                })
                if r.status_code != 200:
                    raise ItauError(f"Falha no extrato Itaú ({r.status_code}): {r.text[:200]}")
                body = r.json()
                for bloco in body.get("data", []):
                    for ev in bloco.get("events", []):
                        p = _parse_evento(ev)
                        if p:
                            eventos.append(p)
                pag = body.get("pagination") or {}
                total_pages = pag.get("total_pages") or 1
                if page >= total_pages:
                    break
                page += 1
    return eventos
