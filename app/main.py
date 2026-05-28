"""
SubiektGT AI Integration — punkt wejścia aplikacji FastAPI.
Uruchomienie: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import init_analytics_db, test_connections
from app.scheduler import setup_scheduler
from app.routers import bi, wms

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup i shutdown hooks."""
    logger.info("🚀 SubiektGT AI — start")
    test_connections()
    init_analytics_db()
    setup_scheduler()
    yield
    logger.info("🛑 SubiektGT AI — zatrzymanie")


app = FastAPI(
    title="SubiektGT AI Integration",
    description="API integrujące SubiektGT z AI (BI) i WMS (kompletacja zamówień)",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS — zezwalaj na połączenia z aplikacji mobilnej WMS w sieci lokalnej
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # W produkcji podaj konkretne IP/domeny
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routery
app.include_router(bi.router)
app.include_router(wms.router)

# Statyczny frontend WMS
try:
    app.mount("/wms", StaticFiles(directory="frontend/wms", html=True), name="wms")
except Exception:
    logger.warning("Frontend WMS nie znaleziony w frontend/wms/ — pomiń jeśli nie zbudowany.")


@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <html><body style="font-family:Arial;padding:40px;background:#1a1a2e;color:#fff">
    <h1>🤖 SubiektGT AI Integration</h1>
    <p>API działa poprawnie.</p>
    <ul>
      <li><a href="/docs" style="color:#4fc3f7">📖 Dokumentacja API (Swagger)</a></li>
      <li><a href="/api/bi/dashboard/summary" style="color:#4fc3f7">📊 BI Dashboard summary</a></li>
      <li><a href="/api/wms/orders/pending" style="color:#4fc3f7">📦 WMS — zamówienia do kompletacji</a></li>
      <li><a href="/wms" style="color:#4fc3f7">📱 Aplikacja WMS (magazynierzy)</a></li>
    </ul>
    </body></html>
    """


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/admin/etl/run")
def manual_etl(date_from: str = None, date_to: str = None):
    """Ręczne uruchomienie ETL (np. po pierwszej instalacji)."""
    from app.services.etl_service import run_full_etl
    return run_full_etl(date_from, date_to)
