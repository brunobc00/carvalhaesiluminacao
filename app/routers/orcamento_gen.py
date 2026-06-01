import os
import re
import sys
import subprocess
import tempfile
import uuid
from datetime import date
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from orcamento_deps import _get_session, require_page, WORKSPACE

router = APIRouter()

_pdf_cache: dict = {}  # download_token → pdf_path


def _fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class OrcamentoRequest(BaseModel):
    items:             list[dict]
    total:             float
    client_name:       str
    client_cnpj:       str = ""
    validade:          str = ""
    descricao_projeto:    str = ""
    informacoes_tecnicas: str = ""
    observacoes:          str = ""
    pagamento:         list[dict] = []
    endereco:          str = ""
    responsavel:       str = ""
    telefone:          str = ""
    email_contato:     str = ""


@router.post("/api/orcamento/gerar-pdf", dependencies=[Depends(require_page("orcamentos"))])
async def gerar_pdf_api(body: OrcamentoRequest):

    today = date.today().strftime("%d/%m/%Y")

    table_rows = ""
    for item in body.items:
        table_rows += (
            f"| {item['ambiente']} | {item['grupo']} | {item['qtd']} "
            f"| {_fmt_brl(item['vunit'])} | {_fmt_brl(item['vtotal'])} |\n"
        )
    table_rows += f"| **TOTAL GERAL** | | | | **{_fmt_brl(body.total)}** |\n"

    pagamento_md = ""
    if body.pagamento:
        parcela_num = ["Entrada"] + [f"{i}ª Parcela" for i in range(1, len(body.pagamento))]
        rows_pag    = ""
        for label, p in zip(parcela_num, body.pagamento):
            rows_pag += f"| {label} | {p['prazo']} | {_fmt_brl(p['valor'])} |\n"
        pagamento_md = f"""\
## 💳 Condições de Pagamento

| Parcela | Prazo | Valor |
| :--- | :--- | :--- |
{rows_pag}
---

"""

    descricao_projeto_md = ""
    if body.descricao_projeto.strip():
        descricao_projeto_md = f"""\
## 📋 Descrição do Projeto

{body.descricao_projeto.strip()}

---

"""

    inf_tec_md = ""
    if body.informacoes_tecnicas.strip():
        inf_tec_md = f"""\
## ⚙️ Informações Técnicas

{body.informacoes_tecnicas.strip()}

---

"""

    info_adicionais_md = ""
    if body.observacoes.strip():
        info_adicionais_md = f"""\
## 📝 Informações Adicionais

{body.observacoes.strip()}

---

"""

    date_parts    = [f"<strong>DATA:</strong> {today}"]
    if body.validade:
        date_parts.append(f"<strong>VALIDADE:</strong> {body.validade}")
    date_line_html = (
        '<div style="text-align:right; font-size:0.88em; margin-bottom:0.6em; color:#444;">'
        + " &nbsp;&nbsp;&nbsp; ".join(date_parts)
        + "</div>"
    )

    cnpj_line    = f"**CNPJ:** {body.client_cnpj}  \n" if body.client_cnpj    else ""
    end_line     = f"**ENDEREÇO:** {body.endereco}  \n" if body.endereco      else ""
    resp_line    = f"**RESPONSÁVEL:** {body.responsavel}  \n" if body.responsavel else ""
    contact_parts = []
    if body.telefone:      contact_parts.append(f"**Tel:** {body.telefone}")
    if body.email_contato: contact_parts.append(f"**E-mail:** {body.email_contato}")
    contact_line = "  &nbsp;&nbsp;|&nbsp;&nbsp;  ".join(contact_parts) + "  \n" if contact_parts else ""

    md_content = f"""# ORÇAMENTO DE ILUMINAÇÃO - {body.client_name.upper()}

{date_line_html}

**CLIENTE:** {body.client_name.upper()}
{cnpj_line}{end_line}{resp_line}{contact_line}
---

{descricao_projeto_md}{inf_tec_md}## 📦 Itens do Orçamento

| Ambiente | Grupo | Qtd | Valor Unit. | Total |
| :--- | :--- | :---: | :--- | :--- |
{table_rows}
---

{pagamento_md}{info_adicionais_md}---

## 🖋️ Confirmação de Pedido
Confirmo os valores, condições de pagamentos e quantidades dos produtos acima relacionados.
"""

    tmp_dir = Path(tempfile.mkdtemp(prefix="orcamento_"))
    md_path = tmp_dir / "orcamento.md"
    md_path.write_text(md_content, encoding="utf-8")

    script = Path(os.getenv(
        "PDF_SCRIPT_PATH",
        str(WORKSPACE / "scripts" / "gerar_orcamento.py")
    ))
    py_bin = Path(sys.executable)

    result = subprocess.run(
        [str(py_bin), str(script), str(md_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar PDF: {result.stderr}")

    pdf_path = tmp_dir / "orcamento.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=500, detail="PDF não foi gerado")

    token = str(uuid.uuid4())
    _pdf_cache[token] = str(pdf_path)

    safe_name = re.sub(r"[^a-z0-9-]", "-", body.client_name.lower())
    return {"token": token, "filename": f"orcamento-{safe_name}.pdf"}


@router.get("/api/orcamento/download/{token}")
async def download_pdf(token: str):
    pdf_path = _pdf_cache.get(token)
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF não encontrado ou expirado")
    return FileResponse(pdf_path, media_type="application/pdf", filename="orcamento.pdf")
