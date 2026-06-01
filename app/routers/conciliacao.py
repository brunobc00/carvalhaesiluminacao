import io
import math
import warnings
from datetime import datetime, date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query, Form
from pydantic import BaseModel
from sqlalchemy import func, and_

from database import get_session
from models import (
    ConciliacaoExtrato, ConciliacaoTransacao, ConciliacaoItau,
    ConciliacaoOmieTitulo, ConciliacaoResultado,
)
from orcamento_deps import _get_session, require_page

warnings.filterwarnings("ignore")

router = APIRouter()


# ── helpers ─────────────────────────────────────────────────────────────────

def _require(request: Request):
    s = _get_session(request)
    if not s:
        raise HTTPException(status_code=401, detail="Não autenticado")
    pags = s.get("paginas", [])
    if "*" not in pags and "conciliacao" not in pags:
        raise HTTPException(status_code=403, detail="Sem acesso")
    return s


def _safe_float(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _safe_val(v, default=0.0):
    """float seguro para serialização JSON — converte None/NaN/Inf para default."""
    try:
        f = float(v) if v is not None else default
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _safe_str(v):
    """string segura — converte NaN/None para ''."""
    if v is None:
        return ""
    try:
        if math.isnan(float(v)):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _safe_date(v):
    if pd.isna(v):
        return None
    if isinstance(v, datetime):
        return v
    try:
        return pd.to_datetime(v).to_pydatetime()
    except Exception:
        return None


# ── detecção e parsing de arquivo ───────────────────────────────────────────

def _detectar_fonte(filename: str, xl: pd.ExcelFile) -> str:
    fn = filename.lower()
    sheets = [s.lower() for s in xl.sheet_names]

    if "lançamentos" in fn or "lancamentos" in fn:
        return "itau"
    if "cielo" in fn or "ciello" in fn:
        return "cielo"
    if "rede_rel" in fn or "rede rel" in fn:
        return "rede"
    if "titulos" in fn and "omie" in fn:
        return "omie"

    if "recebiveis_cielo" in " ".join(sheets):
        return "cielo"
    if "pagamentos" in sheets and "cancelamentos e contestações" in sheets:
        return "rede"
    if any("lançamentos" in s or "lancamentos" in s for s in sheets):
        return "itau"
    if any("contas por cliente" in s for s in sheets):
        return "omie"

    raise ValueError(f"Não foi possível detectar o tipo de arquivo: {filename}")


def _parse_cielo(xl: pd.ExcelFile) -> pd.DataFrame:
    df = xl.parse("Recebiveis_cielo_detalhe1")
    for c in ["Valor bruto", "Taxa/tarifa", "Valor líquido", "Taxa total (%)"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Data de pagamento"] = pd.to_datetime(df.get("Data de pagamento"), errors="coerce")
    df["Data do lançamento"] = pd.to_datetime(df.get("Data do lançamento"), errors="coerce")
    return df


def _parse_rede(xl: pd.ExcelFile) -> pd.DataFrame:
    raw = xl.parse("pagamentos", header=0)
    raw.columns = raw.iloc[0].tolist()
    df = raw.iloc[1:].reset_index(drop=True)
    df = df.dropna(subset=["data do recebimento"])
    for c in ["valor bruto da parcela original", "valor MDR descontado", "valor líquido da parcela"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["data do recebimento"] = pd.to_datetime(df.get("data do recebimento"), errors="coerce")
    df["data original da venda"] = pd.to_datetime(df.get("data original da venda"), errors="coerce")
    return df


def _parse_itau(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = next(s for s in xl.sheet_names if "lançamento" in s.lower() or "lancamento" in s.lower())
    df = xl.parse(sheet)
    df["Data"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")
    df["Valor (R$)"] = pd.to_numeric(df["Valor (R$)"], errors="coerce")
    return df


def _parse_omie(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = xl.sheet_names[0]
    raw = xl.parse(sheet, header=2)
    raw.columns = raw.iloc[0].tolist()
    df = raw.iloc[1:].reset_index(drop=True)
    df["Data de Vencimento (completa)"] = pd.to_datetime(df.get("Data de Vencimento (completa)"), dayfirst=True, errors="coerce")
    df["Data de Emissão (completa)"] = pd.to_datetime(df.get("Data de Emissão (completa)"), dayfirst=True, errors="coerce")
    for c in ["Valor da Conta", "Pago ou Recebido", "A Pagar ou Receber"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ── importar ao banco ────────────────────────────────────────────────────────

def _importar_cielo(df: pd.DataFrame, extrato_id: int, session):
    rows = []
    for _, r in df.iterrows():
        vb = _safe_float(r.get("Valor bruto"))
        taxa = _safe_float(r.get("Taxa/tarifa"))
        taxa_pct = round(-taxa / vb * 100, 4) if vb and vb > 0 and taxa else None
        rows.append(ConciliacaoTransacao(
            extrato_id=extrato_id,
            fonte="cielo",
            data_venda=_safe_date(r.get("Data do lançamento")),
            data_pagamento=_safe_date(r.get("Data de pagamento")),
            tipo=str(r.get("Tipo de lançamento", "") or "")[:150],
            forma_pagamento=str(r.get("Forma de pagamento", "") or "")[:150],
            bandeira=str(r.get("Bandeira", "") or "")[:50],
            valor_bruto=vb,
            taxa=taxa,
            valor_liquido=_safe_float(r.get("Valor líquido")),
            taxa_pct_real=taxa_pct,
            status_pagamento=str(r.get("Status de pagamento", "") or "")[:50],
            nsu_doc=str(r.get("NSU/DOC", "") or "")[:100],
            codigo_venda=str(r.get("Código da venda", "") or "")[:100],
        ))
    session.bulk_save_objects(rows)


def _importar_rede(df: pd.DataFrame, extrato_id: int, session):
    rows = []
    for _, r in df.iterrows():
        vb = _safe_float(r.get("valor bruto da parcela original"))
        mdr = _safe_float(r.get("valor MDR descontado"))
        taxa = -mdr if mdr else None
        taxa_pct = _safe_float(r.get("taxa MDR"))
        taxa_pct_pct = round(float(taxa_pct) * 100, 4) if taxa_pct else None
        rows.append(ConciliacaoTransacao(
            extrato_id=extrato_id,
            fonte="rede",
            data_venda=_safe_date(r.get("data original da venda")),
            data_pagamento=_safe_date(r.get("data do recebimento")),
            tipo=str(r.get("modalidade", "") or "")[:150],
            forma_pagamento=str(r.get("modalidade", "") or "")[:150],
            bandeira=str(r.get("bandeira", "") or "")[:50],
            valor_bruto=vb,
            taxa=taxa,
            valor_liquido=_safe_float(r.get("valor líquido da parcela")),
            taxa_pct_real=taxa_pct_pct,
            status_pagamento=str(r.get("status", "") or "")[:50],
            nsu_doc=str(r.get("NSU/CV", "") or "")[:100],
            codigo_venda=str(r.get("TID", "") or "")[:100],
        ))
    session.bulk_save_objects(rows)


def _importar_itau(df: pd.DataFrame, extrato_id: int, session):
    rows = []
    for _, r in df.iterrows():
        lancamento = str(r.get("Lançamento", "") or "")
        if "CIELO" in lancamento.upper():
            fo = "cielo"
        elif any(k in lancamento.upper() for k in ("REDE", "REDECARD", "SISDEB")):
            fo = "rede"
        else:
            fo = "outros"
        rows.append(ConciliacaoItau(
            extrato_id=extrato_id,
            data=_safe_date(r.get("Data")),
            lancamento=lancamento[:300],
            razao_social=str(r.get("Razão Social", "") or "")[:300],
            valor=_safe_float(r.get("Valor (R$)")),
            fonte_operadora=fo,
        ))
    session.bulk_save_objects(rows)


def _importar_omie(df: pd.DataFrame, extrato_id: int, session):
    rows = []
    for _, r in df.iterrows():
        conta = _safe_str(r.get("Conta Corrente", ""))
        if "CIELO" in conta.upper():
            fo = "cielo"
        elif "REDECARD" in conta.upper() or "REDE" in conta.upper():
            fo = "rede"
        else:
            continue  # só importa títulos de cartão
        valor = _safe_float(r.get("Valor da Conta"))
        if valor is None or valor <= 0:
            continue
        d_em  = _safe_date(r.get("Data de Emissão (completa)"))
        d_ven = _safe_date(r.get("Data de Vencimento (completa)"))
        # garante emissão ≤ vencimento (Omie às vezes exporta invertido)
        if d_em and d_ven and d_em > d_ven:
            d_em, d_ven = d_ven, d_em
        rows.append(ConciliacaoOmieTitulo(
            extrato_id=extrato_id,
            cliente=_safe_str(r.get("Cliente ou Fornecedor (Nome Fantasia)"))[:500],
            data_emissao=d_em,
            data_vencimento=d_ven,
            conta_operadora=conta[:100],
            fonte_operadora=fo,
            forma_pagamento=_safe_str(r.get("Forma de Pagamento"))[:100],
            valor=valor,
            valor_pago=_safe_float(r.get("Pago ou Recebido")) or 0,
            valor_pendente=_safe_float(r.get("A Pagar ou Receber")) or 0,
            conciliado=_safe_str(r.get("Conciliado")).lower() == "sim",
        ))
    session.bulk_save_objects(rows)


# ── reconciliação ────────────────────────────────────────────────────────────

def _reconciliar(session):
    session.query(ConciliacaoResultado).delete()

    trans = session.query(ConciliacaoTransacao).all()
    itau  = session.query(ConciliacaoItau).filter(ConciliacaoItau.valor > 0).all()
    omie  = session.query(ConciliacaoOmieTitulo).all()

    # agrupar transações por (fonte, data_pagamento)
    from collections import defaultdict
    trans_pgto: dict = defaultdict(lambda: {"qtd": 0, "bruto": 0.0, "liquido": 0.0})
    for t in trans:
        if t.data_pagamento and t.status_pagamento and "pago" in t.status_pagamento.lower():
            k = (t.fonte, t.data_pagamento.date())
            trans_pgto[k]["qtd"] += 1
            trans_pgto[k]["bruto"] += float(t.valor_bruto or 0)
            trans_pgto[k]["liquido"] += float(t.valor_liquido or 0)

    # agrupar itaú por (fonte_operadora, data)
    itau_grp: dict = defaultdict(float)
    for i in itau:
        if i.data:
            itau_grp[(i.fonte_operadora, i.data.date())] += float(i.valor or 0)

    # agrupar transações por (fonte, data_venda)
    trans_venda: dict = defaultdict(lambda: {"qtd": 0, "bruto": 0.0})
    for t in trans:
        if t.data_venda:
            k = (t.fonte, t.data_venda.date())
            trans_venda[k]["qtd"] += 1
            trans_venda[k]["bruto"] += float(t.valor_bruto or 0)

    # agrupar omie por (fonte_operadora, data_emissao)
    omie_grp: dict = defaultdict(lambda: {"qtd": 0, "total": 0.0})
    for o in omie:
        if o.data_emissao:
            k = (o.fonte_operadora, o.data_emissao.date())
            omie_grp[k]["qtd"] += 1
            omie_grp[k]["total"] += float(o.valor or 0)

    resultados = []

    # vs_itau
    all_dates_pgto = set(trans_pgto.keys()) | set(itau_grp.keys())
    for (fo, d) in all_dates_pgto:
        tp = trans_pgto.get((fo, d), {})
        it = itau_grp.get((fo, d), 0)
        liq = tp.get("liquido", 0)
        bruto = tp.get("bruto", 0)
        if not liq and not bruto:
            status = "SEM_FONTE"
        elif not it:
            status = "SEM_ITAU"
        else:
            diff = abs(liq - it)
            status = "OK" if diff <= 0.10 else ("DIFER_PEQUENA" if diff <= 5 else "DIVERGENCIA")
        resultados.append(ConciliacaoResultado(
            fonte=fo, tipo="vs_itau", data=datetime.combine(d, datetime.min.time()),
            qtd_fonte=tp.get("qtd", 0), valor_fonte=round(liq, 2),
            valor_destino=round(it, 2), diferenca=round(liq - it, 2), status=status,
        ))

    # vs_omie
    all_dates_venda = set(trans_venda.keys()) | set(omie_grp.keys())
    for (fo, d) in all_dates_venda:
        tv = trans_venda.get((fo, d), {})
        om = omie_grp.get((fo, d), {})
        bruto = tv.get("bruto", 0)
        total_omie = om.get("total", 0)
        if not bruto:
            status = "SEM_FONTE"
        elif not total_omie:
            status = "NAO_OMIE"
        else:
            diff = abs(bruto - total_omie)
            status = "OK" if diff <= 0.10 else ("DIFER_PEQUENA" if diff <= 10 else "DIVERGENCIA")
        resultados.append(ConciliacaoResultado(
            fonte=fo, tipo="vs_omie", data=datetime.combine(d, datetime.min.time()),
            qtd_fonte=tv.get("qtd", 0), valor_fonte=round(bruto, 2),
            valor_destino=round(total_omie, 2), diferenca=round(bruto - total_omie, 2), status=status,
        ))

    session.bulk_save_objects(resultados)
    session.commit()
    return len(resultados)


# ── endpoints ────────────────────────────────────────────────────────────────

@router.post("/api/conciliacao/upload")
async def upload_extrato(request: Request, file: UploadFile = File(...), fonte: str = Form("")):
    sess = _require(request)
    email = sess.get("email", "desconhecido")

    content = await file.read()
    try:
        xl = pd.ExcelFile(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(400, f"Arquivo inválido: {e}")

    if not fonte:
        try:
            fonte = _detectar_fonte(file.filename or "", xl)
        except ValueError as e:
            raise HTTPException(422, str(e))
    elif fonte not in ("itau", "cielo", "rede", "omie"):
        raise HTTPException(400, f"Fonte inválida: {fonte}")

    # dedup por nome de arquivo + fonte
    with get_session() as s:
        dup = s.query(ConciliacaoExtrato).filter_by(filename=file.filename, fonte=fonte).first()
        if dup:
            raise HTTPException(409, f"Arquivo '{file.filename}' já importado para {fonte.upper()} em {dup.importado_em.strftime('%d/%m/%Y %H:%M') if dup.importado_em else '?'}")

    with get_session() as s:
        extrato = ConciliacaoExtrato(
            fonte=fonte,
            filename=file.filename,
            importado_por=email,
        )
        s.add(extrato)
        s.flush()
        eid = extrato.id

        try:
            if fonte == "cielo":
                df = _parse_cielo(xl)
                _importar_cielo(df, eid, s)
                qtd = len(df)
                p_ini = df["Data de pagamento"].min()
                p_fim = df["Data de pagamento"].max()
            elif fonte == "rede":
                df = _parse_rede(xl)
                _importar_rede(df, eid, s)
                qtd = len(df)
                p_ini = df["data do recebimento"].min()
                p_fim = df["data do recebimento"].max()
            elif fonte == "itau":
                df = _parse_itau(xl)
                _importar_itau(df, eid, s)
                qtd = len(df)
                p_ini = df["Data"].min()
                p_fim = df["Data"].max()
            elif fonte == "omie":
                df = _parse_omie(xl)
                _importar_omie(df, eid, s)
                qtd = len(s.query(ConciliacaoOmieTitulo).filter_by(extrato_id=eid).all())
                emissao = df["Data de Emissão (completa)"]
                p_ini = emissao.min()
                p_fim = emissao.max()
        except Exception as e:
            raise HTTPException(500, f"Erro ao processar {fonte}: {e}")

        extrato.qtd_registros = qtd
        if pd.notna(p_ini):
            extrato.periodo_inicio = p_ini.to_pydatetime() if hasattr(p_ini, "to_pydatetime") else p_ini
        if pd.notna(p_fim):
            extrato.periodo_fim = p_fim.to_pydatetime() if hasattr(p_fim, "to_pydatetime") else p_fim
        s.commit()

        n = _reconciliar(s)

    return {"ok": True, "fonte": fonte, "registros": qtd, "resultados_calculados": n}


@router.post("/api/conciliacao/reconciliar")
async def reconciliar(request: Request):
    _require(request)
    with get_session() as s:
        n = _reconciliar(s)
    return {"ok": True, "resultados": n}


@router.get("/api/conciliacao/resumo")
async def resumo(request: Request):
    _require(request)
    with get_session() as s:
        def cnt(model, **kw):
            q = s.query(func.count()).select_from(model)
            for k, v in kw.items():
                q = q.filter(getattr(model, k) == v)
            return q.scalar() or 0

        def soma(model, col, **kw):
            q = s.query(func.coalesce(func.sum(getattr(model, col)), 0)).select_from(model)
            for k, v in kw.items():
                q = q.filter(getattr(model, k) == v)
            return _safe_val(q.scalar())

        extratos = s.query(ConciliacaoExtrato).order_by(ConciliacaoExtrato.importado_em.desc()).all()

        def res_cnt(tipo, status):
            return s.query(func.count()).select_from(ConciliacaoResultado).filter(
                ConciliacaoResultado.tipo == tipo,
                ConciliacaoResultado.status == status
            ).scalar() or 0

        return {
            "extratos": [{"id": e.id, "fonte": e.fonte, "filename": e.filename,
                          "qtd": e.qtd_registros, "importado_em": e.importado_em.isoformat() if e.importado_em else None}
                         for e in extratos],
            "cielo": {
                "total_transacoes": cnt(ConciliacaoTransacao, fonte="cielo"),
                "total_bruto": soma(ConciliacaoTransacao, "valor_bruto", fonte="cielo"),
                "total_taxa": soma(ConciliacaoTransacao, "taxa", fonte="cielo"),
                "total_liquido": soma(ConciliacaoTransacao, "valor_liquido", fonte="cielo"),
                "titulos_omie": cnt(ConciliacaoOmieTitulo, fonte_operadora="cielo"),
                "pendentes_baixa": cnt(ConciliacaoOmieTitulo, fonte_operadora="cielo", marcado_baixa=False) if True else 0,
            },
            "rede": {
                "total_transacoes": cnt(ConciliacaoTransacao, fonte="rede"),
                "total_bruto": soma(ConciliacaoTransacao, "valor_bruto", fonte="rede"),
                "total_taxa": soma(ConciliacaoTransacao, "taxa", fonte="rede"),
                "total_liquido": soma(ConciliacaoTransacao, "valor_liquido", fonte="rede"),
                "titulos_omie": cnt(ConciliacaoOmieTitulo, fonte_operadora="rede"),
            },
            "itau": {
                "total_creditos_cielo": soma(ConciliacaoItau, "valor", fonte_operadora="cielo"),
                "total_creditos_rede": soma(ConciliacaoItau, "valor", fonte_operadora="rede"),
            },
            "omie_pendentes": {
                "cielo": {
                    "qtd": s.query(func.count()).select_from(ConciliacaoOmieTitulo).filter(
                        ConciliacaoOmieTitulo.fonte_operadora == "cielo",
                        ConciliacaoOmieTitulo.valor_pendente > 0
                    ).scalar() or 0,
                    "valor": _safe_val(s.query(func.coalesce(func.sum(ConciliacaoOmieTitulo.valor_pendente), 0)).filter(
                        ConciliacaoOmieTitulo.fonte_operadora == "cielo",
                        ConciliacaoOmieTitulo.valor_pendente > 0
                    ).scalar()),
                    "marcados_baixa": cnt(ConciliacaoOmieTitulo, fonte_operadora="cielo", marcado_baixa=True),
                },
                "rede": {
                    "qtd": s.query(func.count()).select_from(ConciliacaoOmieTitulo).filter(
                        ConciliacaoOmieTitulo.fonte_operadora == "rede",
                        ConciliacaoOmieTitulo.valor_pendente > 0
                    ).scalar() or 0,
                    "valor": _safe_val(s.query(func.coalesce(func.sum(ConciliacaoOmieTitulo.valor_pendente), 0)).filter(
                        ConciliacaoOmieTitulo.fonte_operadora == "rede",
                        ConciliacaoOmieTitulo.valor_pendente > 0
                    ).scalar()),
                    "marcados_baixa": cnt(ConciliacaoOmieTitulo, fonte_operadora="rede", marcado_baixa=True),
                },
            },
            "resultados": {
                "cielo_itau_ok": res_cnt("vs_itau", "OK"),
                "cielo_itau_div": res_cnt("vs_itau", "DIVERGENCIA"),
                "cielo_itau_sem": res_cnt("vs_itau", "SEM_ITAU"),
                "cielo_omie_ok": res_cnt("vs_omie", "OK"),
                "cielo_omie_nao": res_cnt("vs_omie", "NAO_OMIE"),
                "cielo_omie_div": res_cnt("vs_omie", "DIVERGENCIA"),
            },
        }


@router.get("/api/conciliacao/dias")
async def dias(request: Request,
               tipo: str = Query("vs_itau"),
               fonte: str = Query("cielo"),
               status: Optional[str] = Query(None)):
    _require(request)
    with get_session() as s:
        q = s.query(ConciliacaoResultado).filter(
            ConciliacaoResultado.tipo == tipo,
            ConciliacaoResultado.fonte == fonte,
        )
        if status:
            q = q.filter(ConciliacaoResultado.status == status)
        rows = q.order_by(ConciliacaoResultado.data.desc()).all()
        return [
            {
                "id": r.id,
                "data": r.data.strftime("%d/%m/%Y") if r.data else None,
                "qtd_fonte": r.qtd_fonte,
                "valor_fonte": float(r.valor_fonte or 0),
                "valor_destino": float(r.valor_destino or 0),
                "diferenca": float(r.diferenca or 0),
                "status": r.status,
            }
            for r in rows
        ]


@router.get("/api/conciliacao/contas-operadoras")
async def contas_operadoras(request: Request, fonte: str = Query("")):
    _require(request)
    with get_session() as s:
        q = s.query(ConciliacaoOmieTitulo.conta_operadora).distinct()
        if fonte:
            q = q.filter(ConciliacaoOmieTitulo.fonte_operadora == fonte)
        rows = q.order_by(ConciliacaoOmieTitulo.conta_operadora).all()
        return [r[0] for r in rows if r[0]]


@router.get("/api/conciliacao/titulos-pendentes")
async def titulos_pendentes(
    request: Request,
    fonte: str = Query(""),                      # "" = todos | "cielo" | "rede"
    apenas_pendentes: bool = Query(True),
    data_inicio: Optional[str] = Query(None),    # DD/MM/YYYY ou YYYY-MM-DD (data_emissao)
    data_fim: Optional[str] = Query(None),
    conta: Optional[str] = Query(None),
    cliente: Optional[str] = Query(None),
    busca: Optional[str] = Query(None),          # full-text: cliente, conta, forma_pgto
    itau_ok: Optional[str] = Query(None),        # "sim" | "nao" | None = todos
    baixa: Optional[str] = Query(None),          # "sim" | "nao" | None = todos
    sort: str = Query("emissao"),                # emissao | vencimento | valor | cliente | conta | valor_total | pago
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
):
    _require(request)

    def _parse_date(s: str) -> Optional[datetime]:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s.strip(), fmt)
            except Exception:
                pass
        return None

    with get_session() as s:
        # ── mapa itaú (inclui todas as fontes se fonte=="") ──────────
        itau_q = s.query(ConciliacaoItau).filter(ConciliacaoItau.valor > 0)
        if fonte:
            itau_q = itau_q.filter(ConciliacaoItau.fonte_operadora == fonte)
        itau_datas: dict[date, float] = {}
        for i in itau_q.all():
            if i.data:
                d = i.data.date()
                itau_datas[d] = itau_datas.get(d, 0) + _safe_val(i.valor)

        # ── query base ───────────────────────────────────────────────
        q = s.query(ConciliacaoOmieTitulo)
        if fonte:
            q = q.filter(ConciliacaoOmieTitulo.fonte_operadora == fonte)
        if apenas_pendentes:
            q = q.filter(ConciliacaoOmieTitulo.valor_pendente > 0)
        if data_inicio:
            d0 = _parse_date(data_inicio)
            if d0:
                q = q.filter(ConciliacaoOmieTitulo.data_emissao >= d0)
        if data_fim:
            d1 = _parse_date(data_fim)
            if d1:
                q = q.filter(ConciliacaoOmieTitulo.data_emissao <= d1)
        if conta:
            q = q.filter(ConciliacaoOmieTitulo.conta_operadora == conta)
        if cliente:
            q = q.filter(ConciliacaoOmieTitulo.cliente.ilike(f"%{cliente}%"))
        if busca:
            from sqlalchemy import or_
            b = f"%{busca}%"
            q = q.filter(or_(
                ConciliacaoOmieTitulo.cliente.ilike(b),
                ConciliacaoOmieTitulo.conta_operadora.ilike(b),
                ConciliacaoOmieTitulo.forma_pagamento.ilike(b),
            ))
        if baixa == "sim":
            q = q.filter(ConciliacaoOmieTitulo.marcado_baixa == True)
        elif baixa == "nao":
            q = q.filter(ConciliacaoOmieTitulo.marcado_baixa == False)

        # ── ordenação ────────────────────────────────────────────────
        col_map = {
            "emissao":     ConciliacaoOmieTitulo.data_emissao,
            "vencimento":  ConciliacaoOmieTitulo.data_vencimento,
            "valor":       ConciliacaoOmieTitulo.valor_pendente,
            "valor_total": ConciliacaoOmieTitulo.valor,
            "pago":        ConciliacaoOmieTitulo.valor_pago,
            "cliente":     ConciliacaoOmieTitulo.cliente,
            "conta":       ConciliacaoOmieTitulo.conta_operadora,
        }
        sort_col = col_map.get(sort, ConciliacaoOmieTitulo.data_emissao)
        q = q.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

        total = q.count()

        # ── filtro itau_ok (pós-query pois depende do mapa) ──────────
        # carrega tudo filtrado e aplica itau_ok em memória antes de paginar
        all_rows = q.all()

        def _row_to_dict(r):
            venc_date = r.data_vencimento.date() if r.data_vencimento else None
            itau_val = itau_datas.get(venc_date) if venc_date else None
            return {
                "id": r.id,
                "cliente": r.cliente or "",
                "data_emissao": r.data_emissao.strftime("%d/%m/%Y") if r.data_emissao else None,
                "data_vencimento": r.data_vencimento.strftime("%d/%m/%Y") if r.data_vencimento else None,
                "data_vencimento_iso": r.data_vencimento.date().isoformat() if r.data_vencimento else None,
                "conta_operadora": r.conta_operadora or "",
                "forma_pagamento": r.forma_pagamento or "",
                "valor": _safe_val(r.valor),
                "valor_pago": _safe_val(r.valor_pago),
                "valor_pendente": _safe_val(r.valor_pendente),
                "conciliado": r.conciliado,
                "marcado_baixa": r.marcado_baixa,
                "marcado_em": r.marcado_em.strftime("%d/%m/%Y %H:%M") if r.marcado_em else None,
                "marcado_por": r.marcado_por or "",
                "itau_confirmado": itau_val is not None and not math.isnan(itau_val),
                "itau_valor_data": round(itau_val, 2) if (itau_val and not math.isnan(itau_val)) else None,
            }

        result = [_row_to_dict(r) for r in all_rows]

        if itau_ok == "sim":
            result = [r for r in result if r["itau_confirmado"]]
        elif itau_ok == "nao":
            result = [r for r in result if not r["itau_confirmado"]]

        total_filtrado = len(result)
        total_pendente = round(sum(r["valor_pendente"] for r in result), 2)
        total_marcados = sum(1 for r in result if r["marcado_baixa"])

        offset = (page - 1) * per_page
        pagina = result[offset: offset + per_page]

        return {
            "total": total_filtrado,
            "total_pendente": total_pendente,
            "total_marcados": total_marcados,
            "page": page,
            "per_page": per_page,
            "pages": max(1, -(-total_filtrado // per_page)),
            "items": pagina,
        }


class MarcarBaixaBody(BaseModel):
    marcar: bool = True


@router.post("/api/conciliacao/titulos/{titulo_id}/marcar-baixa")
async def marcar_baixa(titulo_id: int, body: MarcarBaixaBody, request: Request):
    sess = _require(request)
    email = sess.get("email", "desconhecido")
    with get_session() as s:
        t = s.query(ConciliacaoOmieTitulo).filter_by(id=titulo_id).first()
        if not t:
            raise HTTPException(404, "Título não encontrado")
        t.marcado_baixa = body.marcar
        t.marcado_em = datetime.utcnow() if body.marcar else None
        t.marcado_por = email if body.marcar else None
        s.commit()
    return {"ok": True, "marcado": body.marcar}


@router.get("/api/conciliacao/extratos")
async def listar_extratos(request: Request):
    _require(request)
    with get_session() as s:
        rows = s.query(ConciliacaoExtrato).order_by(ConciliacaoExtrato.importado_em.desc()).all()
        return [
            {
                "id": e.id,
                "fonte": e.fonte,
                "filename": e.filename,
                "qtd_registros": e.qtd_registros,
                "periodo_inicio": e.periodo_inicio.strftime("%d/%m/%Y") if e.periodo_inicio else None,
                "periodo_fim": e.periodo_fim.strftime("%d/%m/%Y") if e.periodo_fim else None,
                "importado_em": e.importado_em.isoformat() if e.importado_em else None,
                "importado_por": e.importado_por,
            }
            for e in rows
        ]


@router.delete("/api/conciliacao/extratos/{extrato_id}")
async def deletar_extrato(extrato_id: int, request: Request):
    sess = _require(request)
    from orcamento_deps import _is_admin
    if not _is_admin(sess):
        raise HTTPException(403, "Apenas admin")
    with get_session() as s:
        e = s.query(ConciliacaoExtrato).filter_by(id=extrato_id).first()
        if not e:
            raise HTTPException(404, "Extrato não encontrado")
        s.delete(e)
        s.commit()
        _reconciliar(s)
    return {"ok": True}
