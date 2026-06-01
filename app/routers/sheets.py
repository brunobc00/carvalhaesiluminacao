import asyncio
import re
import unicodedata
import urllib.parse
import httpx
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from orcamento_deps import _get_session, _get_google_access_token, require_page

router = APIRouter(dependencies=[Depends(require_page("orcamentos"))])

_TEMPLATE_HEADERS = ["AMBIENTE", "GRUPO", "Quantidade", "Valor Unitário", "Valor Total"]
_TEMPLATE_EXAMPLE = ["Sala", "Spot LED", "4", "300,00", "1.200,00"]

_META_CLIENTES = [
    ["Campo", "Valor"],
    ["Nome", ""], ["CNPJ", ""], ["CEP", ""], ["Endereço", ""],
    ["Responsável", ""], ["Telefone", ""], ["E-mail de Contato", ""], ["Validade", ""],
]
_META_PAGAMENTO = [["Prazo", "Valor", "Observação"]] + [
    [p, "", ""] for p in ["À Vista","28 Dias","30 Dias","60 Dias","90 Dias","120 Dias","150 Dias","180 Dias","210 Dias"]
]
_META_INF_TEC = [["Informações Técnicas"], [""]]


def _parse_brl(s: str) -> float:
    s = s.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _col_letter(n: int) -> str:
    """Converte número de coluna (1-based) em letra(s): 1→A, 26→Z, 27→AA."""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _parse_sheet_data(rows: list, headers_row: list) -> dict:
    def find_col(candidates: list[str], default: int) -> int:
        for name in candidates:
            for i, h in enumerate(headers_row):
                if h.strip().lower() == name.lower():
                    return i
        return default

    col_amb      = find_col(["AMBIENTE", "ambiente"], 2)
    col_grp      = find_col(["GRUPO", "grupo"], 8)
    col_desc     = find_col(["DESCRIÇÃO", "DESCRICAO", "Descrição", "descricao", "descrição"], 11)
    col_ordem    = find_col(["ORDEM", "ordem", "Ordem", "ORDER"], 0)
    col_qtd_exib = find_col(["Quantidade", "QTD", "quantidade"], 5)
    col_vunit    = find_col(["valor unit c desconto", "valor unitario c desc", "valor unit. c/desc",
                              "Valor Unitário", "valor unitario", "vunit"], 19)
    col_vtot     = find_col(["valor total c/desc", "valor total c desc",
                              "Valor Total", "valor total", "vtotal"], 20)
    col_forn     = find_col(["FORNECEDORES", "Fornecedores", "fornecedores",
                              "FORNECEDOR", "Fornecedor", "fornecedor",
                              "FABRICANTE", "Fabricante", "fabricante",
                              "Marca", "MARCA", "marca"], -1)

    header_lower   = {h.strip().lower(): i for i, h in enumerate(headers_row)}
    PAYMENT_LABELS = ["À Vista", "28 Dias", "30 Dias", "60 Dias", "90 Dias", "120 Dias", "150 Dias", "180 Dias", "210 Dias"]
    PAYMENT_FALLBACK = {"À Vista": 29, "28 Dias": -1, "30 Dias": 30, "60 Dias": 31,
                        "90 Dias": 32, "120 Dias": 33, "150 Dias": 34, "180 Dias": 35, "210 Dias": 36}
    payment_cols = [header_lower.get(lbl.lower(), PAYMENT_FALLBACK.get(lbl, -1)) for lbl in PAYMENT_LABELS]
    payment_sums = [0.0] * len(PAYMENT_LABELS)

    linha_map: dict = {}
    for row in rows:
        get      = lambda i, r=row: r[i].strip() if i < len(r) else ""
        ambiente = get(col_amb)
        grupo    = get(col_grp)
        if not ambiente or not grupo:
            continue
        vtot = _parse_brl(get(col_vtot))
        if vtot <= 0:
            continue
        desc  = get(col_desc)
        ordem = get(col_ordem)
        forn  = get(col_forn).strip() if col_forn >= 0 else ""
        qtd7_raw = get(col_qtd_exib)
        chave = (ambiente, grupo, desc, ordem)
        linha_map[chave] = {
            "ambiente": ambiente, "grupo": grupo,
            "qtd7": qtd7_raw,
            "vunit": _parse_brl(get(col_vunit)),
            "vtotal": vtot,
            "forn": forn,
            "row": row,
        }

    for d in linha_map.values():
        row = d["row"]
        get = lambda i, r=row: r[i].strip() if i < len(r) else ""
        for i, col_idx in enumerate(payment_cols):
            if col_idx >= 0:
                payment_sums[i] += _parse_brl(get(col_idx))

    pivot: dict = OrderedDict()
    for (ambiente, grupo, desc, ordem), d in linha_map.items():
        key = (ambiente, grupo)
        if key not in pivot:
            pivot[key] = {"ambiente": ambiente, "grupo": grupo, "qtd": 0, "vunit": 0.0, "vtotal": 0.0, "fornecedores": set()}
        pivot[key]["vtotal"] += d["vtotal"]
        if d["forn"]:
            pivot[key]["fornecedores"].add(d["forn"])
        if d["qtd7"]:
            try:
                pivot[key]["qtd"] += int(d["qtd7"].replace(",", "").split(".")[0])
            except (ValueError, IndexError):
                pass
            if pivot[key]["vunit"] == 0.0:
                pivot[key]["vunit"] = d["vunit"]

    items = [v for v in pivot.values() if v["qtd"] > 0]
    for item in items:
        item["fornecedores"] = sorted(item["fornecedores"])
    total               = sum(i["vtotal"] for i in items)
    fornecedores_unicos = sorted({f for i in items for f in i["fornecedores"]})
    pagamento = [
        {"prazo": label, "valor": round(v, 2)}
        for label, v in zip(PAYMENT_LABELS, payment_sums)
        if v > 0
    ]
    return {"items": items, "total": total, "pagamento": pagamento, "fornecedores": fornecedores_unicos}


async def _aba_escrever(client: httpx.AsyncClient, sid: str, token: str, title: str, values: list,
                        force: bool = False):
    """Cria a aba se não existir e escreve os valores.
    Se a aba já existe e tem dados do usuário (> 1 linha preenchida), preserva — a não ser que force=True.
    """
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    add = await client.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
        headers=hdr,
        json={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    )
    if add.status_code == 401:
        raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
    sheet_exists = add.status_code == 400
    if sheet_exists and not force:
        enc_check = urllib.parse.quote(f"'{title}'!A1:B20", safe="!:'")
        check = await client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc_check}",
            headers=hdr,
        )
        existing = check.json().get("values", []) if check.is_success else []
        filled = sum(1 for row in existing[1:] if any(str(c).strip() for c in row if c))
        if filled > 0:
            return
    if sheet_exists and force:
        enc_clear = urllib.parse.quote(f"'{title}'!A1:Z100", safe="!:'")
        await client.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc_clear}:clear",
            headers=hdr,
        )
    enc = urllib.parse.quote(f"'{title}'!A1", safe="!:'")
    wr  = await client.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc}?valueInputOption=USER_ENTERED",
        headers=hdr,
        json={"values": values},
    )
    if wr.status_code == 401:
        raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
    wr.raise_for_status()


async def _criar_pivot_resumo(client: httpx.AsyncClient, sid: str, token: str) -> bool:
    """Cria aba _ResumoAmbiente com tabela dinâmica nativa baseada em Página1."""
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    meta = await client.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}?fields=sheets.properties",
        headers=hdr,
    )
    if not meta.is_success:
        return False
    sheets_list = meta.json().get("sheets", [])

    def _norm(s: str) -> str:
        return unicodedata.normalize("NFD", s.strip().lower()).encode("ascii", "ignore").decode().replace(" ", "")

    source_props = next(
        (sh["properties"] for sh in sheets_list if _norm(sh["properties"].get("title", "")) in ("pagina1", "page1")),
        None,
    )
    if not source_props:
        return False

    source_gid   = source_props["sheetId"]
    source_title = source_props["title"]
    row_count    = source_props.get("gridProperties", {}).get("rowCount", 1000)
    col_count    = source_props.get("gridProperties", {}).get("columnCount", 50)

    enc = urllib.parse.quote(f"'{source_title}'!A1:AZ1", safe="!:'")
    h_resp = await client.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc}",
        headers=hdr,
    )
    if not h_resp.is_success:
        return False
    header_row = h_resp.json().get("values", [[]])[0]

    def _find_col(candidates: list[str]) -> int:
        for name in candidates:
            for i, h in enumerate(header_row):
                if h.strip().lower() == name.lower():
                    return i
        return -1

    col_amb  = _find_col(["AMBIENTE", "ambiente"])
    col_grp  = _find_col(["GRUPO", "grupo"])
    col_qtd  = _find_col(["QUANTIDADE", "Quantidade", "QTD", "quantidade"])
    col_vtot = _find_col(["valor total", "Valor Total"])  # sem desconto, igual ao pivot manual

    if col_amb < 0 or col_grp < 0 or col_vtot < 0 or col_qtd < 0:
        return False

    vtot_name = header_row[col_vtot].strip()
    qtd_name  = header_row[col_qtd].strip()

    def _ref(name: str) -> str:
        return f"'{name}'" if " " in name else name

    PIVOT_TITLE = "_ResumoAmbiente"
    existing    = next((sh for sh in sheets_list if sh["properties"]["title"] == PIVOT_TITLE), None)
    requests    = []
    if existing:
        requests.append({"deleteSheet": {"sheetId": existing["properties"]["sheetId"]}})
    requests.append({"addSheet": {"properties": {"title": PIVOT_TITLE}}})

    add_resp = await client.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
        headers=hdr,
        json={"requests": requests},
    )
    if not add_resp.is_success:
        return False

    new_sheet_id = add_resp.json()["replies"][-1]["addSheet"]["properties"]["sheetId"]

    pivot_values = [
        {"sourceColumnOffset": col_qtd,  "summarizeFunction": "SUM", "name": "Quantidade"},
        {"sourceColumnOffset": col_vtot, "summarizeFunction": "SUM"},
        {"formula": f"=sum({_ref(vtot_name)})/{_ref(qtd_name)}", "summarizeFunction": "SUM", "name": "Campo 1 calculado"},
    ]

    pivot_resp = await client.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
        headers=hdr,
        json={"requests": [{"updateCells": {
            "rows": [{"values": [{"pivotTable": {
                "source": {
                    "sheetId":          source_gid,
                    "startRowIndex":    0,
                    "startColumnIndex": 0,
                    "endRowIndex":      row_count,
                    "endColumnIndex":   col_count,
                },
                "rows": [
                    {"sourceColumnOffset": col_amb, "sortOrder": "ASCENDING", "repeatHeadings": True},
                    {"sourceColumnOffset": col_grp, "sortOrder": "ASCENDING"},
                ],
                "values":      pivot_values,
                "valueLayout": "HORIZONTAL",
            }}]}],
            "start":  {"sheetId": new_sheet_id, "rowIndex": 0, "columnIndex": 0},
            "fields": "pivotTable",
        }}]},
    )
    return pivot_resp.is_success


# ── Models ────────────────────────────────────────────────────────────────────

class SheetsRequest(BaseModel):
    url: str

class SheetsPasteRequest(BaseModel):
    texto: str

class SheetsCarregarRequest(BaseModel):
    url: str

class SheetsSalvarRequest(BaseModel):
    url:           str
    nome:          str = ""
    cnpj:          str = ""
    cep:           str = ""
    endereco:      str = ""
    responsavel:   str = ""
    telefone:      str = ""
    email_contato: str = ""
    validade:      str = ""
    descricao:             str = ""
    informacoes_tecnicas:  str = ""
    observacoes:           str = ""
    pagamento:             list[dict] = []


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/sheets/processar")
async def sheets_processar(body: SheetsRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    access_token = _get_google_access_token(session["email"])

    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL inválida")
    sid = m.group(1)

    async with httpx.AsyncClient(timeout=45) as client:
        meta = await client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}?fields=sheets.properties",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if meta.status_code == 401:
            raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
        meta.raise_for_status()

        def _is_meta(title: str) -> bool:
            return title.startswith("_")

        sheet_title = None
        col_count   = 50
        sheets_list = meta.json().get("sheets", [])
        all_props   = [sh["properties"] for sh in sheets_list]
        non_meta    = [p for p in all_props if not _is_meta(p.get("title", ""))]

        preferred = next(
            (p for p in non_meta if p.get("title", "").startswith("Orçamento")),
            non_meta[0] if non_meta else None,
        )
        if preferred:
            sheet_title = preferred.get("title", "")
            col_count   = preferred.get("gridProperties", {}).get("columnCount", 50)

        last_col    = _col_letter(col_count)
        sheet_range = f"'{sheet_title}'!A:{last_col}" if sheet_title else f"A:{last_col}"

        encoded_range = urllib.parse.quote(sheet_range, safe="!:'")
        resp = await client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{encoded_range}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
        resp.raise_for_status()

    rows = resp.json().get("values", [])
    if len(rows) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"Aba '{sheet_title}' está vazia ou sem dados suficientes."
        )

    result = _parse_sheet_data(rows[1:], rows[0])
    if not result["items"]:
        header_sample = " | ".join(h.strip() for h in rows[0][:12] if h.strip()) or "(sem cabeçalho)"
        raise HTTPException(
            status_code=422,
            detail=(f"Nenhum item encontrado na aba '{sheet_title}'. "
                    f"Cabeçalhos detectados: [{header_sample}]. "
                    "Verifique se as colunas AMBIENTE e GRUPO existem e se há linhas com Quantidade > 0.")
        )

    async def _ler_meta(tab: str, rng: str = "A1:B30") -> list:
        enc = urllib.parse.quote(f"'{tab}'!{rng}", safe="!:'")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            return r.json().get("values", []) if r.is_success else []
        except Exception:
            return []

    cli_rows, desc_rows, pag_rows, obs_rows = await asyncio.gather(
        _ler_meta("_Clientes"), _ler_meta("_Descricao"), _ler_meta("_Pagamento"), _ler_meta("_Observacoes"),
    )
    result["clientes"]      = {row[0].strip(): (row[1].strip() if len(row) > 1 else "") for row in cli_rows[1:] if row}
    result["descricao"]     = (desc_rows[1][0].strip() if len(desc_rows) > 1 and desc_rows[1] else "")
    result["pagamento_meta"] = [
        {"prazo": row[0].strip(), "valor": round(_parse_brl(row[1]), 2)}
        for row in pag_rows[1:] if len(row) > 1 and row[1].strip() and _parse_brl(row[1]) > 0
    ]
    result["observacoes"] = (obs_rows[1][0].strip() if len(obs_rows) > 1 and obs_rows[1] else "")
    return result


@router.post("/api/sheets/processar-texto")
async def sheets_processar_texto(body: SheetsPasteRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    raw   = body.texto.replace('\r\n', '\n').replace('\r', '\n').strip()
    lines = raw.splitlines()
    if len(lines) < 2:
        raise HTTPException(status_code=400, detail="Dados insuficientes — cole a planilha incluindo o cabeçalho.")

    rows   = [line.split('\t') for line in lines]
    result = _parse_sheet_data(rows[1:], rows[0])
    if not result["items"]:
        raise HTTPException(
            status_code=422,
            detail="Nenhum item encontrado. Verifique se as colunas AMBIENTE e GRUPO estão presentes e se há linhas com Quantidade > 0."
        )
    return result


@router.post("/api/sheets/criar-template")
async def sheets_criar_template(body: SheetsRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL de planilha inválida.")
    sid   = m.group(1)
    token = _get_google_access_token(session["email"])

    async with httpx.AsyncClient(timeout=30) as client:
        add_r = await client.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}:batchUpdate",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"requests": [{"addSheet": {"properties": {"title": "Orçamento — Template"}}}]},
        )
        if add_r.status_code == 401:
            raise HTTPException(status_code=401, detail="Token expirado. Faça login novamente.")
        if add_r.status_code == 400:
            detail = add_r.json().get("error", {}).get("message", "Erro ao criar aba.")
            raise HTTPException(status_code=400, detail=detail)
        add_r.raise_for_status()

        new_gid   = add_r.json()["replies"][0]["addSheet"]["properties"]["sheetId"]
        new_title = "Orçamento — Template"

        encoded_range = urllib.parse.quote(f"'{new_title}'!A1", safe="!:'")
        write_r = await client.put(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{encoded_range}"
            "?valueInputOption=USER_ENTERED",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": [_TEMPLATE_HEADERS, _TEMPLATE_EXAMPLE]},
        )
        write_r.raise_for_status()

    return {"gid": new_gid, "title": new_title}


@router.post("/api/sheets/criar-template-completo")
async def sheets_criar_template_completo(body: SheetsRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL inválida")
    sid, token = m.group(1), _get_google_access_token(session["email"])

    abas = ["Orçamento — Template", "_Clientes", "_Descricao", "_InfTec", "_Pagamento", "_Observacoes"]
    async with httpx.AsyncClient(timeout=60) as client:
        await _aba_escrever(client, sid, token, "Orçamento — Template", [_TEMPLATE_HEADERS, _TEMPLATE_EXAMPLE])
        await _aba_escrever(client, sid, token, "_Clientes",         _META_CLIENTES)
        await _aba_escrever(client, sid, token, "_Descricao",        [["Descrição do Projeto"], [""]])
        await _aba_escrever(client, sid, token, "_InfTec",           _META_INF_TEC)
        await _aba_escrever(client, sid, token, "_Pagamento",        _META_PAGAMENTO)
        await _aba_escrever(client, sid, token, "_Observacoes",      [["Informações Adicionais"], [""]])
        pivot_ok = await _criar_pivot_resumo(client, sid, token)
        if pivot_ok:
            abas.append("_ResumoAmbiente")

    return {"ok": True, "abas": abas}


@router.post("/api/sheets/carregar-completo")
async def sheets_carregar_completo(body: SheetsCarregarRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL inválida")
    sid, token = m.group(1), _get_google_access_token(session["email"])

    async def _ler(tab: str, rng: str = "A1:B30") -> list:
        enc = urllib.parse.quote(f"'{tab}'!{rng}", safe="!:'")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{enc}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return r.json().get("values", []) if r.is_success else []

    cli_rows, desc_rows, inf_rows, pag_rows, obs_rows = await asyncio.gather(
        _ler("_Clientes"), _ler("_Descricao"), _ler("_InfTec"), _ler("_Pagamento", "A1:C30"), _ler("_Observacoes"),
    )

    clientes             = {row[0].strip(): (row[1].strip() if len(row) > 1 else "") for row in cli_rows[1:] if row}
    descricao            = (desc_rows[1][0].strip() if len(desc_rows) > 1 and desc_rows[1] else "")
    informacoes_tecnicas = (inf_rows[1][0].strip() if len(inf_rows) > 1 and inf_rows[1] else "")
    pagamento            = [
        {"prazo": row[0].strip(), "valor": round(_parse_brl(row[1]), 2), "obs": row[2].strip() if len(row) > 2 else ""}
        for row in pag_rows[1:] if len(row) > 1 and row[1].strip() and _parse_brl(row[1]) > 0
    ]
    observacoes          = (obs_rows[1][0].strip() if len(obs_rows) > 1 and obs_rows[1] else "")

    return {"clientes": clientes, "descricao": descricao, "informacoes_tecnicas": informacoes_tecnicas,
            "pagamento": pagamento, "observacoes": observacoes}


@router.post("/api/sheets/salvar-dados")
async def sheets_salvar_dados(body: SheetsSalvarRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL inválida")
    sid, token = m.group(1), _get_google_access_token(session["email"])

    pag_values = [["Prazo", "Valor", "Observação"]] + [
        [p["prazo"], float(p["valor"]), p.get("obs", "")]
        for p in body.pagamento if float(p.get("valor", 0)) > 0
    ]

    async with httpx.AsyncClient(timeout=45) as client:
        await _aba_escrever(client, sid, token, "_Clientes", [
            ["Campo", "Valor"],
            ["Nome", body.nome], ["CNPJ", body.cnpj], ["CEP", body.cep], ["Endereço", body.endereco],
            ["Responsável", body.responsavel], ["Telefone", body.telefone],
            ["E-mail de Contato", body.email_contato], ["Validade", body.validade],
        ], force=True)
        await _aba_escrever(client, sid, token, "_Descricao",   [["Descrição do Projeto"], [body.descricao]], force=True)
        await _aba_escrever(client, sid, token, "_InfTec",      [["Informações Técnicas"], [body.informacoes_tecnicas]], force=True)
        await _aba_escrever(client, sid, token, "_Pagamento",   pag_values, force=True)
        await _aba_escrever(client, sid, token, "_Observacoes", [["Informações Adicionais"], [body.observacoes]], force=True)

    return {"ok": True}


@router.post("/api/sheets/criar-pivot")
async def sheets_criar_pivot(body: SheetsRequest, request: Request):
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", body.url)
    if not m:
        raise HTTPException(status_code=400, detail="URL inválida")
    sid, token = m.group(1), _get_google_access_token(session["email"])

    async with httpx.AsyncClient(timeout=45) as client:
        ok = await _criar_pivot_resumo(client, sid, token)

    if not ok:
        raise HTTPException(
            status_code=422,
            detail="Não foi possível criar a tabela dinâmica. "
                   "Verifique se a planilha tem uma aba chamada 'Página1' com colunas AMBIENTE, GRUPO e Valor Total.",
        )
    return {"ok": True, "aba": "_ResumoAmbiente"}
