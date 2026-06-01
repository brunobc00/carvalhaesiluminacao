import os
import logging
from contextlib import asynccontextmanager

import bcrypt
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from database import engine, SessionLocal, Base
from models import Produto, Orcamento, AdminUser
from routers import produtos, orcamentos, admin
from routers import fornecedores, sheets, orcamento_gen, google_auth, orcamento_ui, conciliacao, ollama, export
from webhook import router as webhook_router
from templates_config import templates
from version_utils import read_version, parse_changelog
from auth import COOKIE_NAME, verify_session_cookie, require_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


def seed_admin():
    """Cria o admin padrão se não existir."""
    db = SessionLocal()
    try:
        if not db.query(AdminUser).first():
            username = os.getenv("ADMIN_USER", "admin")
            password = os.getenv("ADMIN_PASS", "mudar123")
            senha_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            admin_user = AdminUser(username=username, senha_hash=senha_hash)
            db.add(admin_user)
            db.commit()
            logger.info("Admin padrao criado: %s", username)
    finally:
        db.close()


_scheduler = None


def _job_sync_itau():
    try:
        import itau_store
        r = itau_store.sincronizar(full=True)
        logger.info("Job noturno Itaú: %s", r)
    except Exception as e:
        logger.warning("Job noturno Itaú falhou: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Criando tabelas no banco de dados...")
    Base.metadata.create_all(bind=engine)
    logger.info("Tabelas criadas.")
    seed_admin()
    # Scheduler: backfill do extrato Itaú toda noite (03:00)
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        _scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
        _scheduler.add_job(_job_sync_itau, CronTrigger(hour=3, minute=0),
                           id="sync_itau", replace_existing=True, misfire_grace_time=3600)
        _scheduler.start()
        logger.info("Scheduler iniciado (sync_itau 03:00).")
    except Exception as e:
        logger.warning("Scheduler não iniciado: %s", e)
    yield
    # Shutdown
    if _scheduler:
        _scheduler.shutdown(wait=False)
    logger.info("Aplicacao encerrada.")


app = FastAPI(
    title="Carvalhaes Iluminacao",
    description="Site de catalogo de luminárias",
    version=read_version(),
    lifespan=lifespan,
)
app.state.version = read_version()


# Identifica quem está logado para o badge do cabeçalho (todas as páginas).
# Prioriza o login admin (cookie assinado); senão, o e-mail do Cloudflare Access.
@app.middleware("http")
async def identify_user(request: Request, call_next):
    user = None
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            user = verify_session_cookie(token)
        except Exception:
            user = None
    if not user:
        user = request.headers.get("Cf-Access-Authenticated-User-Email")
    request.state.user = user
    return await call_next(request)


# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="/app/uploads"), name="uploads")

# Templates (shared instance from templates_config)

# Routers
app.include_router(produtos.router)
app.include_router(orcamentos.router)
app.include_router(admin.router)
app.include_router(webhook_router)

# Orçamentos internos (migrado de com.automacaobbc.ia)
app.include_router(fornecedores.router)
app.include_router(fornecedores._global_router)
app.include_router(sheets.router)
app.include_router(orcamento_gen.router)
app.include_router(google_auth.router)
app.include_router(orcamento_ui.router)
app.include_router(conciliacao.router)
app.include_router(ollama.router)
app.include_router(export.router)


# ─────────────────────────────────────────────
# Página inicial
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    db = SessionLocal()
    try:
        destaques = (
            db.query(Produto)
            .filter(Produto.ativo == True)
            .order_by(Produto.criado_em.desc())
            .limit(6)
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(request, "index.html", {
        "destaques": destaques,
    })


# ─────────────────────────────────────────────
# Histórico de versões (changelog)
# ─────────────────────────────────────────────

@app.get("/versoes", response_class=HTMLResponse)
def versoes(request: Request, admin: str = Depends(require_admin)):
    return templates.TemplateResponse(request, "versoes.html", {
        "releases": parse_changelog(),
        "admin": admin,
    })


# Redirect /admin → /admin/dashboard
@app.get("/admin")
def admin_root():
    return RedirectResponse(url="/admin/dashboard", status_code=302)


# ─────────────────────────────────────────────
# Exception handler para redirect de auth
# ─────────────────────────────────────────────

from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import RedirectResponse as RR


@app.exception_handler(FastAPIHTTPException)
async def http_exception_handler(request: Request, exc: FastAPIHTTPException):
    if exc.status_code == 302 and exc.headers and "Location" in exc.headers:
        return RR(url=exc.headers["Location"], status_code=302)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
