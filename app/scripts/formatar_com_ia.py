import sys
import os
from pathlib import Path
import json

# Adiciona o caminho do core para importar o wrapper
sys.path.append("/home/bruno/Documentos/Github/bruno-ai-core")
from core.ollama_wrapper import OllamaWrapper

def format_budget(raw_text):
    core = OllamaWrapper()
    
    # Carrega dados da empresa para o contexto
    with open("/home/bruno/Documentos/Github/carvalhaescomercial-orcamentos/dados_empresa.json", "r") as f:
        empresa = json.load(f)

    prompt = f"""
Você é um assistente administrativo da empresa {empresa['nome_fantasia']}.
Sua tarefa é converter o texto bruto de um orçamento em um arquivo Markdown elegante e profissional.

DADOS DA EMPRESA:
{json.dumps(empresa, indent=2)}

TEXTO BRUTO DO ORÇAMENTO:
{raw_text}

INSTRUÇÕES DE FORMATAÇÃO:
1. Use Tabelas Markdown para os itens.
2. Formate os valores monetários como R$ 0.000,00.
3. Inclua uma seção de 'CONDIÇÕES DE PAGAMENTO' em formato de tabela detalhada.
4. Corrija automaticamente erros ortográficos e de digitação (ex: 'Femenino' para 'Feminino', 'Descanço' para 'Descanso').
5. Inclua uma seção final de 'ASSINATURAS' com campos para Carvalhaes e para o Cliente.
5. O título principal deve ser 'ORÇAMENTO DE ILUMINAÇÃO'.

Retorne APENAS o conteúdo do Markdown, sem explicações.
"""

    formatted_md = core.ask(prompt)
    return formatted_md

if __name__ == "__main__":
    raw_data = """
ORÇAMENTO: CONDOMINIO EDIFICIO OZANAM 
CNPJ.: 33761655/0001-31
DATA: 17/04/2026
VALIDADE: 28/04/2026
ORÇAMENTO DE ILUMINAÇÃO DAS ÁREAS COMUNS E VARANDAS.
OBSERVAÇÕES: O PENDENTE DA RECEPÇÃO SERÁ COMO NA DESCRIÇÃO DO PROJETO, (2 MÓDULOS)
CONDIÇÕES GERAIS:
ENTRADA: R$ 50.673,52
PARCELAS: 7x de R$ 26.000,00 (30 a 180 dias)
ITENS:
ACADEMIA | ARANDELA | 3 | R$ 186,14 | R$ 558,42
ACADEMIA | PERFIL | 16 | R$ 255,75 | R$ 4.092,05
... (e o restante da tabela)
"""
    # Nota: Vou passar o texto completo do usuário no comando real.
