"""
Router BI — endpointy analityczne.
Wszystkie dane są serwowane z bazy analitycznej PostgreSQL (nie SubiektGT produkcyjnego).
"""
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_analytics_session, analytics_engine
from app.services.ai_service import ai_query, get_quick_stats
from app.services.forecast_service import forecast_demand, get_reorder_suggestions
from pydantic import BaseModel

router = APIRouter(prefix="/api/bi", tags=["BI — Analityka"])


# ── Helpers ────────────────────────────────────────────────────────────────

def run_query(sql: str, params: dict = None) -> list:
    with analytics_engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    return df.to_dict(orient="records")


# ── Statystyki ogólne (dashboard) ─────────────────────────────────────────

@router.get("/dashboard/summary")
def dashboard_summary():
    """Szybkie KPI do dashboardu głównego."""
    return get_quick_stats()


# ── Sprzedaż ──────────────────────────────────────────────────────────────

@router.get("/sales/summary")
def sales_summary(
    date_from: date = Query(default=(date.today() - timedelta(days=30))),
    date_to: date = Query(default=date.today()),
    group_by: str = Query(default="day", description="day | week | month")
):
    """Sumaryczna sprzedaż w zadanym przedziale czasowym."""
    if group_by == "month":
        trunc = "month"
    elif group_by == "week":
        trunc = "week"
    else:
        trunc = "day"

    sql = f"""
        SELECT
            DATE_TRUNC('{trunc}', data_wystawienia)::date AS okres,
            COUNT(DISTINCT numer_dokumentu)               AS liczba_dokumentow,
            ROUND(SUM(wartosc_netto)::numeric, 2)         AS wartosc_netto,
            ROUND(SUM(marza_netto)::numeric, 2)           AS marza_netto,
            ROUND(AVG(marza_procent)::numeric, 1)         AS marza_procent
        FROM sprzedaz_historia
        WHERE data_wystawienia BETWEEN :df AND :dt
        GROUP BY 1
        ORDER BY 1
    """
    return run_query(sql, {"df": date_from, "dt": date_to})


@router.get("/sales/top-products")
def top_products(
    date_from: date = Query(default=(date.today() - timedelta(days=30))),
    date_to: date = Query(default=date.today()),
    limit: int = Query(default=20, le=100),
    order_by: str = Query(default="wartosc", description="wartosc | ilosc | marza")
):
    """Ranking produktów wg wartości/ilości/marży."""
    order_col = {
        "wartosc": "wartosc_netto",
        "ilosc": "laczna_ilosc",
        "marza": "marza_netto"
    }.get(order_by, "wartosc_netto")

    sql = f"""
        SELECT
            towar_symbol,
            towar_nazwa,
            ROUND(SUM(ilosc)::numeric, 2)           AS laczna_ilosc,
            ROUND(SUM(wartosc_netto)::numeric, 2)   AS wartosc_netto,
            ROUND(SUM(marza_netto)::numeric, 2)     AS marza_netto,
            ROUND(AVG(marza_procent)::numeric, 1)   AS marza_procent
        FROM sprzedaz_historia
        WHERE data_wystawienia BETWEEN :df AND :dt
        GROUP BY towar_symbol, towar_nazwa
        ORDER BY {order_col} DESC
        LIMIT :limit
    """
    return run_query(sql, {"df": date_from, "dt": date_to, "limit": limit})


@router.get("/sales/trend/{towar_symbol}")
def sales_trend(
    towar_symbol: str,
    months: int = Query(default=12, le=36)
):
    """Miesięczny trend sprzedaży produktu."""
    sql = """
        SELECT
            rok, miesiac,
            ROUND(SUM(ilosc)::numeric, 2)         AS ilosc,
            ROUND(SUM(wartosc_netto)::numeric, 2) AS wartosc_netto,
            ROUND(AVG(marza_procent)::numeric, 1) AS marza_procent
        FROM sprzedaz_historia
        WHERE towar_symbol = :symbol
          AND data_wystawienia >= NOW() - (:months * INTERVAL '1 month')
        GROUP BY rok, miesiac
        ORDER BY rok, miesiac
    """
    return run_query(sql, {"symbol": towar_symbol, "months": months})


# ── Klienci ────────────────────────────────────────────────────────────────

@router.get("/customers/top")
def top_customers(
    date_from: date = Query(default=(date.today() - timedelta(days=90))),
    date_to: date = Query(default=date.today()),
    limit: int = Query(default=20, le=100)
):
    """Ranking klientów wg wartości zakupów."""
    sql = """
        SELECT
            kontrahent_id,
            kontrahent_symbol,
            kontrahent_nazwa,
            COUNT(DISTINCT numer_dokumentu)         AS liczba_dokumentow,
            ROUND(SUM(wartosc_netto)::numeric, 2)   AS wartosc_netto,
            ROUND(SUM(marza_netto)::numeric, 2)     AS marza_netto,
            ROUND(AVG(marza_procent)::numeric, 1)   AS marza_procent
        FROM sprzedaz_historia
        WHERE data_wystawienia BETWEEN :df AND :dt
        GROUP BY kontrahent_id, kontrahent_symbol, kontrahent_nazwa
        ORDER BY wartosc_netto DESC
        LIMIT :limit
    """
    return run_query(sql, {"df": date_from, "dt": date_to, "limit": limit})


@router.get("/customers/rfm")
def customers_rfm(days: int = Query(default=365)):
    """
    Segmentacja RFM klientów.
    R = Recency (dni od ostatniego zakupu)
    F = Frequency (liczba dokumentów)
    M = Monetary (łączna wartość)
    """
    sql = """
        SELECT
            kontrahent_id,
            kontrahent_nazwa,
            CURRENT_DATE - MAX(data_wystawienia)::date      AS recency_dni,
            COUNT(DISTINCT numer_dokumentu)                 AS frequency,
            ROUND(SUM(wartosc_netto)::numeric, 2)           AS monetary,
            NTILE(5) OVER (ORDER BY MAX(data_wystawienia) DESC)         AS r_score,
            NTILE(5) OVER (ORDER BY COUNT(DISTINCT numer_dokumentu))    AS f_score,
            NTILE(5) OVER (ORDER BY SUM(wartosc_netto))                 AS m_score
        FROM sprzedaz_historia
        WHERE data_wystawienia >= NOW() - (:days * INTERVAL '1 day')
        GROUP BY kontrahent_id, kontrahent_nazwa
        ORDER BY monetary DESC
        LIMIT 200
    """
    return run_query(sql, {"days": days})


# ── Magazyn ────────────────────────────────────────────────────────────────

@router.get("/stock/current")
def stock_current(min_stan: float = Query(default=0)):
    """Bieżące stany magazynowe (ostatni snapshot)."""
    sql = """
        SELECT towar_symbol, towar_nazwa, stan, zarezerwowane, dostepne, wartosc_zakupu
        FROM stany_magazynowe
        WHERE data_snapshotu = (SELECT MAX(data_snapshotu) FROM stany_magazynowe)
          AND stan > :min_stan
        ORDER BY wartosc_zakupu DESC
    """
    return run_query(sql, {"min_stan": min_stan})


@router.get("/stock/rotation")
def stock_rotation(
    max_dni: Optional[int] = Query(default=None, description="Tylko towary z rotacją < X dni"),
    limit: int = Query(default=50)
):
    """Wskaźniki rotacji magazynowej."""
    where = "WHERE rotacja_dni < :max_dni" if max_dni else ""
    params = {"limit": limit}
    if max_dni:
        params["max_dni"] = max_dni

    sql = f"""
        SELECT towar_symbol, towar_nazwa, stan_biezacy, srednia_dzienna,
               rotacja_dni, sprzedaz_30d, sprzedaz_90d
        FROM rotacja_towarow
        WHERE data_obliczenia = (SELECT MAX(data_obliczenia) FROM rotacja_towarow)
        {'AND rotacja_dni < :max_dni' if max_dni else ''}
          AND rotacja_dni < 9999
        ORDER BY rotacja_dni ASC
        LIMIT :limit
    """
    return run_query(sql, params)


@router.get("/stock/reorder")
def stock_reorder(min_rotacja_dni: int = Query(default=14)):
    """Sugestie zamówień uzupełniających."""
    return get_reorder_suggestions(min_rotacja_dni)


# ── Prognozowanie ──────────────────────────────────────────────────────────

@router.get("/forecast/{towar_symbol}")
def demand_forecast(
    towar_symbol: str,
    days_ahead: int = Query(default=30, le=90),
    history_days: int = Query(default=365, le=730)
):
    """Prognoza zapotrzebowania na produkt (ML)."""
    return forecast_demand(towar_symbol, days_ahead, history_days)


# ── AI Chat ────────────────────────────────────────────────────────────────

class AIQueryRequest(BaseModel):
    question: str
    use_cache: bool = True


@router.post("/ai/query")
async def ai_bi_query(request: AIQueryRequest):
    """
    Pytanie do AI w języku naturalnym.
    Przykłady:
      - 'Które produkty miały największy spadek sprzedaży w zeszłym miesiącu?'
      - 'Pokaż 10 klientów z najwyższą marżą w Q3'
      - 'Jakie towary mają zapas poniżej 14 dni?'
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Pytanie nie może być puste.")
    return await ai_query(request.question, use_cache=request.use_cache)
