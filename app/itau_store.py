"""
Sincronização do extrato Itaú → banco (idempotente, com auto-colunas).

Regra de ouro: nada que a API retornar se perde.
- O evento cru completo é salvo na coluna JSON `raw` de `conciliacao_itau`.
- Cada campo do evento (achatado) vira uma coluna `x_<campo>` criada automaticamente
  (ALTER TABLE ADD COLUMN IF NOT EXISTS) e é salvo.
- Dedup por `evento_id` (id do evento na API) — pode rodar quantas vezes quiser.

Funções:
- importar_periodo(de, ate): importa um intervalo (YYYY-MM-DD).
- backfill(): varre mês a mês para trás até o Itaú parar de devolver dados.
- sincronizar(full): full=True → backfill; full=False → últimos ~40 dias.
"""
import re
import threading
from datetime import date, timedelta

from sqlalchemy import text

from database import get_session
from models import ConciliacaoExtrato, ConciliacaoItau
import itau_client

_lock = threading.Lock()
_EXTRATO_FILENAME = "API Itaú (sincronização automática)"
_BASE_COLS = {  # colunas já existentes no modelo — não viram x_*
    "id", "extrato_id", "data", "lancamento", "razao_social", "valor",
    "fonte_operadora", "tipo", "documento", "evento_id", "raw",
}


def _safe_col(key: str) -> str:
    return "x_" + re.sub(r"[^a-z0-9_]", "_", str(key).lower())


def _ensure_columns(conn, cols: set[str]) -> None:
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='conciliacao_itau'"
    )).fetchall()
    existing = {r[0] for r in rows}
    for c in cols:
        if c not in existing and c not in _BASE_COLS:
            conn.execute(text(f'ALTER TABLE conciliacao_itau ADD COLUMN IF NOT EXISTS "{c}" text'))


def importar_periodo(de: str, ate: str) -> dict:
    """Importa o extrato do intervalo [de, ate] (YYYY-MM-DD). Idempotente."""
    eventos = itau_client.fetch_eventos(de, ate)
    novos = 0
    with get_session() as s:
        conn = s.connection()
        extrato = (s.query(ConciliacaoExtrato)
                     .filter_by(fonte="itau", filename=_EXTRATO_FILENAME).first())
        if not extrato:
            extrato = ConciliacaoExtrato(fonte="itau", filename=_EXTRATO_FILENAME, importado_por="sync")
            s.add(extrato); s.flush()
        eid = extrato.id

        existing_ids = {r[0] for r in s.query(ConciliacaoItau.evento_id)
                        .filter(ConciliacaoItau.evento_id.isnot(None)).all()}

        flat_cols: set[str] = set()
        for ev in eventos:
            flat_cols.update(_safe_col(k) for k in ev["flat"].keys())
        if flat_cols:
            _ensure_columns(conn, flat_cols)

        for ev in eventos:
            evid = ev.get("evento_id")
            if evid and evid in existing_ids:
                continue
            row = ConciliacaoItau(
                extrato_id=eid, data=ev["data"], lancamento=ev["lancamento"],
                razao_social=ev["razao_social"], valor=ev["valor"],
                fonte_operadora=ev["fonte_operadora"], tipo=ev["tipo"],
                documento=ev["documento"], evento_id=evid, raw=ev["raw"],
            )
            s.add(row); s.flush()
            flat = {_safe_col(k): (None if v is None else str(v)) for k, v in ev["flat"].items()}
            flat = {c: v for c, v in flat.items() if c not in _BASE_COLS}
            if flat:
                sets = ", ".join(f'"{c}"=:v{i}' for i, c in enumerate(flat))
                params = {f"v{i}": v for i, v in enumerate(flat.values())}
                params["rid"] = row.id
                conn.execute(text(f"UPDATE conciliacao_itau SET {sets} WHERE id=:rid"), params)
            if evid:
                existing_ids.add(evid)
            novos += 1

        extrato.qtd_registros = (s.query(ConciliacaoItau).filter_by(extrato_id=eid).count())
        s.commit()

        # reconciliação (import tardio p/ evitar ciclo)
        try:
            from routers.conciliacao import _reconciliar
            with get_session() as s2:
                _reconciliar(s2)
        except Exception:
            pass

    return {"novos": novos, "total_periodo": len(eventos)}


def _mes_anterior(d: date) -> date:
    primeiro = d.replace(day=1)
    return primeiro - timedelta(days=1)


def backfill(max_meses: int = 72) -> dict:
    """Varre mês a mês para trás até o Itaú parar de devolver dados (2 meses vazios seguidos).
    Teto de 72 meses como salvaguarda — o normal é parar antes, quando o Itaú zera."""
    hoje = date.today()
    fim = hoje
    ini = hoje.replace(day=1)
    total_novos = 0
    meses = 0
    vazios = 0
    detalhe = []
    for _ in range(max_meses):
        try:
            r = importar_periodo(ini.isoformat(), fim.isoformat())
        except itau_client.ItauError as e:
            detalhe.append({"de": ini.isoformat(), "ate": fim.isoformat(), "erro": str(e)[:120]})
            break
        meses += 1
        total_novos += r["novos"]
        detalhe.append({"de": ini.isoformat(), "ate": fim.isoformat(), **r})
        if r["total_periodo"] == 0:
            vazios += 1
            if vazios >= 2:
                break
        else:
            vazios = 0
        fim = _mes_anterior(ini)
        ini = fim.replace(day=1)
    return {"meses": meses, "novos": total_novos, "detalhe": detalhe}


def sincronizar(full: bool = False) -> dict:
    """full=True → backfill completo; full=False → últimos ~40 dias. Serializado por lock."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "sincronização já em andamento"}
    try:
        if full:
            return backfill()
        hoje = date.today()
        return importar_periodo((hoje - timedelta(days=40)).isoformat(), hoje.isoformat())
    finally:
        _lock.release()
