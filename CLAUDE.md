# CLAUDE.md — Carvalhaes Iluminação

## O que é este projeto

Site institucional e de catálogo de produtos para a **Carvalhaes Iluminação**.
Permite exibir luminárias com fotos, categorias e preços, receber solicitações de orçamento
e gerenciar tudo via painel administrativo protegido por senha.

## Stack utilizada

| Camada | Tecnologia |
|---|---|
| Web framework | FastAPI + Jinja2 (SSR) |
| CSS | Tailwind CSS via CDN (sem build step) |
| ORM | SQLAlchemy |
| Banco | PostgreSQL 16 (container Docker) |
| Auth | itsdangerous.TimestampSigner (cookie assinado) + bcrypt |
| Deploy | Docker Compose + systemd |
| Auto-deploy | Webhook GitHub → git pull → docker compose up |

## Como rodar localmente

1. Copie o arquivo de variáveis e edite os valores:
   ```bash
   cp .env.example .env
   # edite .env: DB_PASS, SECRET_KEY, WEBHOOK_SECRET, ADMIN_PASS
   ```

2. Suba os containers:
   ```bash
   docker compose up --build -d
   ```

3. Acesse em: `http://localhost:8002`

O banco e as tabelas são criados automaticamente no primeiro startup.
O usuário admin padrão (`admin` / senha definida em `ADMIN_PASS`) é criado automaticamente se não existir.

## Como acessar o painel admin

URL: `http://localhost:8002/admin/login`

Credenciais padrão (definidas no `.env`):
- Usuário: valor de `ADMIN_USER` (padrão: `admin`)
- Senha: valor de `ADMIN_PASS` (padrão: `mudar123` — **troque em produção**)

No painel é possível:
- Criar, editar e excluir produtos (com upload de foto)
- Visualizar orçamentos recebidos e atualizar o status (novo / em análise / respondido)

## Como o auto-deploy funciona

1. O GitHub envia um `POST /webhook/github` após cada push no branch `main`
2. A aplicação verifica a assinatura HMAC-SHA256 usando `WEBHOOK_SECRET`
3. Se válido, dispara em background thread o script `scripts/deploy.sh`:
   ```bash
   cd /project && git pull origin main && docker compose up --build -d
   ```
4. O volume `/home/bruno/Documentos/Github/carvalhaesiluminacao:/project` e o socket
   `/var/run/docker.sock` estão montados no container para que isso funcione.

**Configuração no GitHub:**
- Settings → Webhooks → Add webhook
- Payload URL: `https://<seu-dominio>/webhook/github`
- Content type: `application/json`
- Secret: mesmo valor de `WEBHOOK_SECRET` no `.env`
- Evento: `Just the push event`

## Configuração do Cloudflare Tunnel

1. Acesse o painel Cloudflare Zero Trust → Networks → Tunnels
2. Crie um novo tunnel ou edite o existente
3. Adicione uma entrada pública:
   - Subdomínio/domínio: ex. `iluminacao.seudominio.com.br`
   - Serviço: `http://localhost:8002`
4. Copie o token do tunnel e cole em `CLOUDFLARE_TUNNEL_TOKEN` no `.env`
5. O tunnel é iniciado automaticamente pelo Docker Compose (se quiser adicionar o serviço
   `cloudflared` no `docker-compose.yml` usando a imagem `cloudflare/cloudflared`).

## Estrutura de pastas relevante

```
app/
├── main.py          — entrada da aplicação, startup, seed admin
├── models.py        — Produto, Orcamento, AdminUser (SQLAlchemy)
├── auth.py          — session via TimestampSigner, dependency require_admin
├── webhook.py       — GitHub webhook com verificação HMAC
├── routers/
│   ├── produtos.py  — catálogo público
│   ├── orcamentos.py — formulário público
│   └── admin.py    — CRUD de produtos + gerência de orçamentos
├── templates/       — HTML Jinja2 (base.html + admin/base_admin.html)
├── static/          — CSS e JS customizados
└── uploads/         — fotos dos produtos (volume Docker)
```

## Volumes Docker

| Volume | Finalidade |
|---|---|
| `pgdata` | Dados persistentes do PostgreSQL |
| `uploads` | Fotos dos produtos enviadas pelo admin |
| `/project` | Código-fonte montado para o auto-deploy funcionar |
| `/var/run/docker.sock` | Socket Docker para o webhook executar `docker compose` |
