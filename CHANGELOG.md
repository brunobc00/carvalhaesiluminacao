# Changelog

Todas as mudanças notáveis deste projeto são registradas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/),
e este projeto segue [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.5.0] - 2026-06-01

### Adicionado
- **Sincronização do extrato Itaú** idempotente (dedup pelo id do evento): importação não duplica.
- **Backfill mês a mês**: varre o histórico para trás até o Itaú parar de responder (botão "Backfill histórico").
- **Job noturno** (APScheduler, 03:00) que roda o backfill automaticamente; e **sincronização ao abrir a página** do Itaú (últimos ~40 dias).
- **Regra de ouro**: cada evento é salvo cru e completo na coluna JSON `raw`; e cada campo retornado pela API vira uma coluna `x_<campo>` criada automaticamente — nada se perde.

### Alterado
- Aba Extrato Itaú: período padrão = de hoje até 1 mês atrás.

## [1.4.0] - 2026-06-01

### Adicionado
- Submenu em **Conciliação Financeira** com uma aba por fonte. Primeira: **Extrato Itaú** (`/admin/conciliacao/itau`) — tabela com o dado cru (Data · Tipo · Valor · Documento · Histórico · Contraparte), filtro de data, busca por nome/histórico/documento, filtro por valor (mín/máx) e ordenação por qualquer coluna.
- Campos `tipo` (origin.type: PIX/SAD/…) e `documento` (literal.code) no extrato Itaú importado via API; endpoint `GET /api/conciliacao/itau/extrato`.

## [1.3.0] - 2026-06-01

### Alterado
- Login admin agora aceita o e-mail já autenticado pelo **Cloudflare Access** (header `Cf-Access-Authenticated-User-Email`): quem passa pelo Zero Trust entra no painel **sem digitar usuário/senha**. A senha continua como alternativa (acesso direto/local).
- Página `/versoes` movida para **dentro do painel** (protegida por admin, com item no menu lateral).

### Segurança
- Histórico de versões (`/versoes`) e o badge de versão **não são mais exibidos no site público** — só no painel admin.

## [1.2.0] - 2026-06-01

### Adicionado
- **Conciliação Financeira** (`/admin/conciliacao`): importação de extratos Cielo/Rede/Itaú/Omie (XLSX) e reconciliação, migrada do `com.automacaobbc.ia`.
- **Integração Itaú (Extrato via API)**: cliente nativo mTLS + OAuth (`itau_client.py`), botão "Importar via API" no card Itaú e endpoint `POST /api/conciliacao/itau/importar-api`.
- **Fornecedores** (`/admin/fornecedores`) e **Catálogo** (`/admin/catalogo`): tabelas de preço com parsing (pandas/pdfplumber) e fallback de visão via Ollama.
- **Gerador de Orçamentos** (`/admin/orcamento`): planilha (Google Sheets/colar) → PDF; endpoints Ollama (`/api/ollama/*`, `/api/instrucoes-llama`) para os botões de IA.

### Alterado
- Repositório renomeado para `com.automacaobbc.carvalhaesiluminacao` (deploy, volume de uploads externo, deploy-hook).
- Acesso ao domínio restrito via Cloudflare Zero Trust (apenas e-mails autorizados).
- Ollama apontando para o PC com GPU na LAN (`192.168.1.251`), não o servidor.

### Corrigido
- Página `/versoes` retornava 500 (`sec.items` colidia com o método de dict no Jinja → `sec['items']`).

## [1.1.0] - 2026-06-01

### Adicionado
- Badge no canto superior direito mostrando o usuário logado e a versão do site.
- Página `/versoes` com o histórico de versões (este changelog).

## [1.0.0] - 2026-05-01

### Adicionado
- Site de catálogo de luminárias (FastAPI + Postgres).
- Painel administrativo: produtos, orçamentos, fornecedores e catálogo.
- Gerador de orçamentos em PDF.
- Integração com Google (Sheets/OAuth) e webhook de deploy.
