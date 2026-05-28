"""
Zarządzanie połączeniami z bazami danych:
  - MS SQL Server (SubiektGT) — tylko odczyt
  - PostgreSQL (analityczna) — odczyt i zapis
"""
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from app.config import settings

logger = logging.getLogger(__name__)


# ── SubiektGT (MS SQL) ─────────────────────────────────────────────────────

subiekt_engine = create_engine(
    settings.subiekt_conn_str,
    poolclass=QueuePool,
    pool_size=3,           # małe pool — tylko odczyt
    max_overflow=2,
    pool_pre_ping=True,    # sprawdza połączenie przed użyciem
    echo=settings.debug,
)

SubiektSession = sessionmaker(bind=subiekt_engine, autocommit=False, autoflush=False)


# ── PostgreSQL analityczny ──────────────────────────────────────────────────

analytics_engine = create_engine(
    settings.analytics_conn_str,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.debug,
)

AnalyticsSession = sessionmaker(bind=analytics_engine, autocommit=False, autoflush=False)


class AnalyticsBase(DeclarativeBase):
    pass


# ── Context managers ────────────────────────────────────────────────────────

@contextmanager
def get_subiekt_db():
    """Sesja do odczytu danych z SubiektGT. Zawsze tylko SELECT."""
    session = SubiektSession()
    try:
        yield session
    except Exception as e:
        logger.error(f"Błąd połączenia z SubiektGT: {e}")
        raise
    finally:
        session.close()


@contextmanager
def get_analytics_db():
    """Sesja do bazy analitycznej PostgreSQL."""
    session = AnalyticsSession()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Błąd bazy analitycznej: {e}")
        raise
    finally:
        session.close()


# ── FastAPI dependency injection ────────────────────────────────────────────

def get_analytics_session():
    """Dependency dla FastAPI endpoint'ów."""
    with get_analytics_db() as session:
        yield session


# ── Inicjalizacja bazy analitycznej ────────────────────────────────────────

def init_analytics_db():
    """Tworzy tabele w bazie analitycznej jeśli nie istnieją."""
    from app.models.analytics import AnalyticsBase  # import lokalny by uniknąć circular
    AnalyticsBase.metadata.create_all(bind=analytics_engine)
    logger.info("Baza analityczna zainicjalizowana.")


def test_connections():
    """Testuje oba połączenia — wywołaj przy starcie aplikacji."""
    try:
        with subiekt_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Połączenie z SubiektGT: OK")
    except Exception as e:
        logger.error(f"❌ Brak połączenia z SubiektGT: {e}")

    try:
        with analytics_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Połączenie z PostgreSQL analitycznym: OK")
    except Exception as e:
        logger.error(f"❌ Brak połączenia z PostgreSQL: {e}")
