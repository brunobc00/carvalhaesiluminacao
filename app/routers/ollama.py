import json
import os
import re
from pathlib import Path

import httpx
from difflib import SequenceMatcher
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from orcamento_deps import _get_session, require_page, require_session, OLLAMA_URL, OLLAMA_BASE, OLLAMA_MODEL

router = APIRouter()

INSTRUCOES_FILE = Path(os.getenv("INSTRUCOES_LLAMA_FILE", "/app/uploads/instrucoes_llama.json"))


def _extrair_garantias(instrucao: str) -> tuple[dict[str, str], str]:
    """Extrai {nome: garantia} e fallback de uma instrução com regras no formato 'Nome = X anos'."""
    mapa: dict[str, str] = {}
    fallback = "garantia do fabricante"
    for linha in re.split(r"[|\n]", instrucao):
        m = re.search(r"^[-*\s]*(.+?)\s*[=:]\s*(.+)$", linha.strip())
        if not m:
            continue
        nome  = m.group(1).strip().strip("*").strip()
        valor = m.group(2).strip().rstrip(".")
        if re.match(r"^(outros?|demais|other)", nome, re.IGNORECASE):
            fallback = valor
        elif nome:
            mapa[nome] = valor
    return mapa, fallback


def _resolver_garantias(fornecedores: list[str], instrucao_base: str) -> list[str] | None:
    """Cruza fornecedores com regras de garantia; retorna linhas pré-resolvidas ou None."""
    mapa, fallback = _extrair_garantias(instrucao_base)
    if not mapa:
        return None
    resultado = []
    for forn in fornecedores:
        f_norm   = forn.lower().replace(" ", "")
        garantia = None
        melhor_ratio = 0.0
        melhor_g     = None
        for nome_regra, g in mapa.items():
            n_norm = nome_regra.lower().replace(" ", "")
            if n_norm in f_norm or f_norm in n_norm:
                garantia = g
                break
            ratio = SequenceMatcher(None, f_norm, n_norm).ratio()
            if ratio > melhor_ratio:
                melhor_ratio = ratio
                melhor_g     = g
        if garantia is None and melhor_ratio >= 0.70:
            garantia = melhor_g
        resultado.append(f"- {forn}: {garantia or fallback}")
    return resultado


def _build_melhorar_prompt(
    texto: str, modo: str, instrucao: str,
    instrucoes_globais: str = "", instrucao_base: str = "",
    fornecedores: list[str] | None = None,
) -> str:
    resolved: list[str] | None = None
    if fornecedores and instrucao_base.strip():
        resolved = _resolver_garantias(fornecedores, instrucao_base)

    ctx_parts = []
    if instrucoes_globais.strip():
        ctx_parts.append(f"Contexto do orçamento:\n{instrucoes_globais.strip()}")
    if instrucao_base.strip() and not resolved:
        ctx_parts.append(f"Diretriz para este campo:\n{instrucao_base.strip()}")
    if fornecedores and not resolved:
        ctx_parts.append(
            f"Fornecedores REAIS presentes neste orçamento "
            f"(mencione SOMENTE estes): {', '.join(fornecedores)}"
        )
    ctx = ("\n\n" + "\n\n".join(ctx_parts) + "\n") if ctx_parts else ""

    if modo == "minimo":
        return (
            "Corrija erros de ortografia e gramática (português brasileiro) e formate em Markdown básico.\n"
            "Regras:\n"
            "- Use **negrito** apenas para termos técnicos e títulos\n"
            "- Corrija SOMENTE erros de escrita — não altere, resuma ou expanda o conteúdo\n"
            "- Retorne SOMENTE o texto corrigido, sem comentários ou prefixos\n"
            f"{ctx}\nTexto:\n{texto}"
        )
    if modo == "livre":
        base = f"Texto base:\n{texto}" if texto else "(sem texto base — crie a partir da instrução)"
        return (
            "Você é um assistente de redação para orçamentos comerciais de iluminação.\n"
            f"Instrução: {instrucao}\n"
            f"{ctx}\n"
            "Regras:\n"
            "- Português brasileiro, tom profissional e formal\n"
            "- Formate em Markdown: **negrito** para termos técnicos, listas quando adequado\n"
            "- Preserve informações técnicas do texto base (se houver)\n"
            "- Retorne SOMENTE o texto, sem prefixos ou explicações\n\n"
            f"{base}"
        )
    # modo "completo"
    if resolved:
        ctx_global  = f"\nContexto: {instrucoes_globais.strip()}\n" if instrucoes_globais.strip() else ""
        forn_block  = "Fornecedores e garantias presentes neste orçamento (mencione TODOS):\n" + "\n".join(resolved)
        return (
            "Você é um assistente de redação para orçamentos comerciais de iluminação.\n"
            f"{ctx_global}\n"
            f"{forn_block}\n\n"
            "Escreva 1 parágrafo mencionando TODOS os fornecedores acima com suas garantias. "
            "Tom profissional, português brasileiro. "
            "Não invente fornecedores, produtos ou especificações técnicas. "
            "Não crie listas, subtítulos ou seções. "
            "Retorne somente o parágrafo, sem prefixos.\n\n"
            f"Texto base:\n{texto}"
        )
    diretriz_extra = (
        "IMPORTANTE: A 'Diretriz para este campo' acima tem prioridade absoluta. "
        "Siga-a rigorosamente — não adicione seções, listas ou informações além do que ela determina.\n"
    ) if instrucao_base.strip() else ""
    return (
        "Você é um assistente especializado em redação profissional para orçamentos comerciais de iluminação.\n"
        f"{ctx}\n"
        f"{diretriz_extra}"
        "Melhore o texto abaixo seguindo estas regras:\n"
        "1. Corrija erros de digitação e gramática (português brasileiro)\n"
        "2. Use Markdown apenas se necessário: **negrito** para termos técnicos, listas somente se o texto original já tiver enumerações\n"
        "3. Mantenha tom profissional e formal, adequado para um documento comercial\n"
        "4. NÃO invente produtos, especificações, categorias ou informações que não estejam no texto original\n"
        "5. NÃO adicione seções, subtítulos ou blocos extras além do que for pedido na diretriz do campo\n"
        "6. Se o texto já estiver bem escrito, faça apenas ajustes mínimos necessários\n\n"
        "Retorne SOMENTE o texto melhorado em Markdown, sem explicações, sem prefixos como 'Aqui está:' ou similares.\n\n"
        f"Texto original:\n{texto}"
    )


# ── Models ────────────────────────────────────────────────────────────────────

class MelhorarTextoRequest(BaseModel):
    texto:              str
    modo:               str = "completo"
    instrucao:          str = ""
    instrucoes_globais: str = ""
    instrucao_base:     str = ""
    fornecedores:       list[str] = []


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/ollama/status", dependencies=[Depends(require_session)])
async def ollama_status():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            ps = await client.get(f"{OLLAMA_BASE}/api/ps")
            ps.raise_for_status()
            modelos_ativos = [m["name"] for m in ps.json().get("models", [])]
            carregado = any(OLLAMA_MODEL in m for m in modelos_ativos)
            return {"online": True, "carregado": carregado, "model": OLLAMA_MODEL, "ativos": modelos_ativos}
    except Exception:
        return {"online": False, "carregado": False, "model": OLLAMA_MODEL, "ativos": []}


@router.post("/api/ollama/carregar", dependencies=[Depends(require_page("orcamentos"))])
async def ollama_carregar():
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": "", "keep_alive": -1, "stream": False},
            )
            r.raise_for_status()
        return {"ok": True, "model": OLLAMA_MODEL}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ollama não respondeu: {e}")


@router.post("/api/ollama/melhorar-texto", dependencies=[Depends(require_page("orcamentos"))])
async def ollama_melhorar_texto(body: MelhorarTextoRequest):

    texto = body.texto.strip()
    modo  = body.modo if body.modo in ("minimo", "completo", "livre") else "completo"

    if modo != "livre" and not texto:
        raise HTTPException(status_code=400, detail="Texto vazio.")
    if modo == "livre" and not body.instrucao.strip() and not texto:
        raise HTTPException(status_code=400, detail="No modo livre informe uma instrução ou texto base.")

    prompt = _build_melhorar_prompt(
        texto, modo, body.instrucao.strip(),
        body.instrucoes_globais.strip(), body.instrucao_base.strip(),
        body.fornecedores or [],
    )

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            OLLAMA_URL,
            json={"model": "llama3.1:8b", "prompt": prompt, "stream": False},
        )

    if not r.is_success:
        raise HTTPException(status_code=502, detail="Ollama não respondeu. Verifique se o serviço está ativo.")

    sugestao = r.json().get("response", "").strip()
    if not sugestao:
        raise HTTPException(status_code=502, detail="Ollama retornou resposta vazia.")

    return {"sugestao": sugestao}


# ── Instruções globais para o LLM (usadas pelo gerador) ───────────────────────

class InstrucoesLlamaBody(BaseModel):
    descricao:   str = ""
    observacoes: str = ""


@router.get("/api/instrucoes-llama", dependencies=[Depends(require_session)])
async def get_instrucoes_llama():
    if INSTRUCOES_FILE.exists():
        data = json.loads(INSTRUCOES_FILE.read_text(encoding="utf-8"))
        return {"descricao": data.get("descricao", ""), "observacoes": data.get("observacoes", "")}
    return {"descricao": "", "observacoes": ""}


@router.post("/api/instrucoes-llama", dependencies=[Depends(require_session)])
async def save_instrucoes_llama(body: InstrucoesLlamaBody):
    existing = {}
    if INSTRUCOES_FILE.exists():
        try:
            existing = json.loads(INSTRUCOES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update({"descricao": body.descricao, "observacoes": body.observacoes})
    INSTRUCOES_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTRUCOES_FILE.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}
