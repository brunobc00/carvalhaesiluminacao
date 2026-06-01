# Carvalhaes Iluminação

Site institucional e catálogo de produtos para a **Carvalhaes Iluminação**.
Permite exibir luminárias com fotos, categorias e preços, receber solicitações de
orçamento e gerenciar tudo por um painel administrativo protegido por senha.

## ✨ Funcionalidades

- **Catálogo público** de luminárias com fotos, categorias e preços
- **Página inicial** com produtos em destaque
- **Formulário de orçamento** para clientes solicitarem propostas
- **Painel administrativo** protegido por login para:
  - Criar, editar e excluir produtos (com upload de foto)
  - Visualizar orçamentos recebidos e atualizar o status (novo / em análise / respondido)
- **Auto-deploy** via webhook do GitHub a cada push no branch `main`

## 🛠️ Stack

| Camada | Tecnologia |
|---|---|
| Web framework | FastAPI + Jinja2 (renderização no servidor) |
| CSS | Tailwind CSS via CDN (sem build step) |
| ORM | SQLAlchemy |
| Banco de dados | PostgreSQL 16 |
| Autenticação | `itsdangerous.TimestampSigner` (cookie assinado) + bcrypt |
| Deploy | Docker Compose + systemd |
| Auto-deploy | Webhook GitHub → `git pull` → `docker compose up` |

## 🚀 Como rodar localmente

1. Copie o arquivo de variáveis de ambiente e edite os valores:

   ```bash
   cp .env.example .env
   # edite .env: DB_PASS, SECRET_KEY, WEBHOOK_SECRET, ADMIN_PASS
   ```

2. Suba os containers:

   ```bash
   docker compose up --build -d
   ```

3. Acesse em: <http://localhost:8002>

> O banco e as tabelas são criados automaticamente no primeiro startup.
> O usuário admin padrão é criado automaticamente caso não exista.

## 🔐 Painel administrativo

- **URL:** <http://localhost:8002/admin/login>
- **Usuário:** valor de `ADMIN_USER` (padrão: `admin`)
- **Senha:** valor de `ADMIN_PASS` (padrão: `mudar123` — **troque em produção!**)

## ⚙️ Variáveis de ambiente

Definidas no arquivo `.env` (veja `.env.example` como modelo):

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | String de conexão do PostgreSQL |
| `DB_USER` / `DB_PASS` | Credenciais do banco de dados |
| `SECRET_KEY` | Chave para assinatura dos cookies de sessão |
| `WEBHOOK_SECRET` | Segredo compartilhado com o webhook do GitHub |
| `ADMIN_USER` / `ADMIN_PASS` | Credenciais do admin padrão |
| `CLOUDFLARE_TUNNEL_TOKEN` | Token do Cloudflare Tunnel (opcional) |

## 📁 Estrutura do projeto

```
app/
├── main.py            — entrada da aplicação, startup, seed do admin
├── database.py        — engine e sessão do SQLAlchemy
├── models.py          — Produto, Orcamento, AdminUser
├── auth.py            — sessão via TimestampSigner, dependency require_admin
├── webhook.py         — webhook do GitHub com verificação HMAC
├── templates_config.py — instância compartilhada do Jinja2
├── routers/
│   ├── produtos.py    — catálogo público
│   ├── orcamentos.py  — formulário público de orçamento
│   └── admin.py       — CRUD de produtos + gerência de orçamentos
├── templates/         — HTML Jinja2 (base.html + admin/base_admin.html)
├── static/            — CSS e JS customizados
└── uploads/           — fotos dos produtos (volume Docker)
```

## 🔄 Auto-deploy

1. O GitHub envia um `POST /webhook/github` após cada push no branch `main`.
2. A aplicação verifica a assinatura HMAC-SHA256 usando `WEBHOOK_SECRET`.
3. Se válido, dispara em background o script `scripts/deploy.sh`:

   ```bash
   git pull origin main && docker compose up --build -d
   ```

**Configuração no GitHub** (Settings → Webhooks → Add webhook):

- Payload URL: `https://<seu-dominio>/webhook/github`
- Content type: `application/json`
- Secret: mesmo valor de `WEBHOOK_SECRET` no `.env`
- Evento: *Just the push event*

## 🌐 Cloudflare Tunnel (opcional)

1. No painel Cloudflare Zero Trust → **Networks → Tunnels**, crie ou edite um tunnel.
2. Adicione uma entrada pública apontando para `http://localhost:8002`.
3. Copie o token do tunnel para `CLOUDFLARE_TUNNEL_TOKEN` no `.env`.

## 💾 Volumes Docker

| Volume | Finalidade |
|---|---|
| `pgdata` | Dados persistentes do PostgreSQL |
| `uploads` | Fotos dos produtos enviadas pelo admin |
| `/project` | Código-fonte montado para o auto-deploy |
| `/var/run/docker.sock` | Socket Docker para o webhook executar `docker compose` |
