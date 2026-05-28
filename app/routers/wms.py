"""
Router WMS — endpointy do zarządzania kompletacją zamówień.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.wms_service import (
    get_pending_orders,
    get_optimized_picklist,
    verify_and_confirm_pick,
    complete_picking_session
)
from app.database import analytics_engine
from sqlalchemy import text
import pandas as pd

router = APIRouter(prefix="/api/wms", tags=["WMS — Magazyn"])


# ── Zamówienia ─────────────────────────────────────────────────────────────

@router.get("/orders/pending")
def pending_orders(magazyn_id: int = Query(default=1)):
    """Lista zamówień (ZO) gotowych do kompletacji."""
    return get_pending_orders(magazyn_id)


@router.get("/order/{order_id}/picklist")
def order_picklist(
    order_id: int,
    algorithm: str = Query(default="nearest_neighbor", description="nearest_neighbor | snake")
):
    """
    Zoptymalizowana lista kompletacyjna dla zamówienia.
    Zwraca pozycje w kolejności minimalizującej drogę w magazynie.
    """
    result = get_optimized_picklist(order_id, algorithm)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Sesja kompletacji ──────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    zamowienie_id: int
    operator_login: str


@router.post("/session/start")
def start_session(req: StartSessionRequest):
    """Rozpoczyna sesję kompletacji dla zamówienia."""
    from datetime import datetime
    with analytics_engine.connect() as conn:
        result = conn.execute(text("""
            INSERT INTO wms_sesje_kompletacji
              (zamowienie_id, operator_login, data_rozpoczecia, status)
            VALUES (:zid, :op, :ts, 'w_toku')
            RETURNING id
        """), {
            "zid": req.zamowienie_id,
            "op": req.operator_login,
            "ts": datetime.utcnow()
        })
        sesja_id = result.fetchone()[0]
        conn.commit()

    return {"sesja_id": sesja_id, "status": "w_toku"}


# ── Potwierdzanie skanowania ───────────────────────────────────────────────

class ConfirmPickRequest(BaseModel):
    sesja_id: int
    pozycja_id: int
    scan_code: str          # zeskanowany EAN lub symbol towaru
    ilosc: float
    operator_login: str


@router.post("/picking/confirm")
def confirm_pick(req: ConfirmPickRequest):
    """
    Weryfikuje skan kodu kreskowego i potwierdza zebranie pozycji.
    Zwraca ok=True/False z komunikatem dla magazyniera.
    """
    return verify_and_confirm_pick(
        sesja_id=req.sesja_id,
        pozycja_id=req.pozycja_id,
        scan_code=req.scan_code,
        ilosc=req.ilosc,
        operator_login=req.operator_login
    )


# ── Zamknięcie kompletacji ─────────────────────────────────────────────────

class CompleteSessionRequest(BaseModel):
    sesja_id: int
    zamowienie_id: int
    operator_login: str


@router.post("/session/complete")
def complete_session(req: CompleteSessionRequest):
    """Zamyka sesję kompletacji i inicjuje wystawienie WZ."""
    return complete_picking_session(req.sesja_id, req.zamowienie_id, req.operator_login)


# ── Mapa magazynu i lokalizacje ────────────────────────────────────────────

@router.get("/warehouse/locations")
def warehouse_locations(magazyn_id: int = Query(default=1)):
    """Wszystkie aktywne lokalizacje w magazynie."""
    with analytics_engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT * FROM wms_lokalizacje WHERE aktywna = true AND magazyn_id = :mid ORDER BY rzad, regał, polka"),
            conn, params={"mid": magazyn_id}
        )
    return df.to_dict(orient="records")


@router.get("/location/{kod}/stock")
def location_stock(kod: str):
    """Towary przypisane do konkretnej lokalizacji."""
    with analytics_engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT tl.towar_symbol, tl.towar_id, tl.lokalizacja_kod,
                       s.stan, s.dostepne
                FROM wms_towar_lokalizacja tl
                LEFT JOIN stany_magazynowe s ON s.towar_id = tl.towar_id
                    AND s.data_snapshotu = (SELECT MAX(data_snapshotu) FROM stany_magazynowe)
                WHERE tl.lokalizacja_kod = :kod AND tl.aktywne = true
            """),
            conn, params={"kod": kod}
        )
    return df.to_dict(orient="records")


class AssignLocationRequest(BaseModel):
    towar_id: int
    towar_symbol: str
    lokalizacja_kod: str
    jest_domyslna: bool = True


@router.post("/location/assign")
def assign_location(req: AssignLocationRequest):
    """Przypisuje towar do lokalizacji w magazynie."""
    with analytics_engine.connect() as conn:
        # Pobierz koordynaty lokalizacji
        loc = conn.execute(
            text("SELECT pozycja_x, pozycja_y FROM wms_lokalizacje WHERE kod = :kod"),
            {"kod": req.lokalizacja_kod}
        ).fetchone()

        if not loc:
            raise HTTPException(status_code=404, detail=f"Lokalizacja {req.lokalizacja_kod} nie istnieje.")

        conn.execute(text("""
            INSERT INTO wms_towar_lokalizacja
              (towar_id, towar_symbol, lokalizacja_kod, lokalizacja_x, lokalizacja_y, jest_domyslna, aktywne)
            VALUES (:tid, :sym, :lok, :x, :y, :def, true)
            ON CONFLICT (towar_id, lokalizacja_kod) DO UPDATE
              SET aktywne = true, jest_domyslna = :def
        """), {
            "tid": req.towar_id,
            "sym": req.towar_symbol,
            "lok": req.lokalizacja_kod,
            "x": float(loc[0]),
            "y": float(loc[1]),
            "def": req.jest_domyslna
        })
        conn.commit()

    return {"ok": True, "message": f"Towar {req.towar_symbol} przypisany do {req.lokalizacja_kod}"}


# ── Statystyki WMS ────────────────────────────────────────────────────────

@router.get("/stats/today")
def wms_stats_today():
    """Statystyki kompletacji z dzisiaj."""
    with analytics_engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT
                COUNT(*)                                AS sesje_lacznie,
                COUNT(*) FILTER (WHERE status='zakonczona') AS sesje_zakonczone,
                AVG(czas_sekund) FILTER (WHERE status='zakonczona') AS sredni_czas_s,
                SUM(liczba_bledow)                      AS bledy_lacznie
            FROM wms_sesje_kompletacji
            WHERE DATE(data_rozpoczecia) = CURRENT_DATE
        """), conn)
    return df.to_dict(orient="records")[0] if not df.empty else {}
