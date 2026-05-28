"""
Serwis WMS — logika kompletacji zamówień.
Pobiera zamówienia z SubiektGT, buduje listy kompletacyjne
i zapisuje potwierdzenia/wyniki z powrotem do bazy analitycznej.
"""
import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.database import subiekt_engine, analytics_engine
from app.models.analytics import SesjKompletacji
from app.services.route_optimizer import RouteOptimizer, ItemDoPick, serialize_trasa

logger = logging.getLogger(__name__)

optimizer = RouteOptimizer(entry_point=(0.0, 0.0))


# ── Pobieranie zamówień z SubiektGT ────────────────────────────────────────

def get_pending_orders(magazyn_id: int = None) -> List[dict]:
    """
    Zwraca listę zamówień klientów (ZO) gotowych do kompletacji.
    Kryteria: zatwierdzone (stan=1), niezrealizowane w pełni.
    """
    mag_id = magazyn_id or settings.default_warehouse_id

    query = """
        SELECT
            d.dok_Id                AS id,
            d.dok_NumerPelny        AS numer,
            d.dok_DataWystawienia   AS data,
            d.dok_DataPlatnosci     AS termin,
            k.kh_Nazwa              AS kontrahent,
            k.kh_Telefon            AS telefon,
            COUNT(p.obDokPoz_Id)    AS liczba_pozycji,
            SUM(p.obDokPoz_WartoscNetto) AS wartosc_netto
        FROM dok__Dokument d
        JOIN kh__Kontrahent k ON k.kh_Id = d.dok_KontrahentId
        JOIN dok__PozycjaDokumentu p ON p.obDokPoz_DokumentId = d.dok_Id
        WHERE d.dok_TypDokumentuSymbol = 'ZO'
          AND d.dok_Stan = 1
          AND d.dok_MagazynId = :mag_id
        GROUP BY d.dok_Id, d.dok_NumerPelny, d.dok_DataWystawienia,
                 d.dok_DataPlatnosci, k.kh_Nazwa, k.kh_Telefon
        ORDER BY d.dok_DataPlatnosci ASC, d.dok_DataWystawienia ASC
    """

    with subiekt_engine.connect() as conn:
        df = pd.read_sql(text(query), conn, params={"mag_id": mag_id})

    return df.to_dict(orient="records")


def get_order_items_with_locations(order_id: int) -> List[ItemDoPick]:
    """
    Pobiera pozycje zamówienia wraz z lokalizacjami z bazy WMS.
    Jeśli towar nie ma przypisanej lokalizacji — trafia na koniec listy (x=9999).
    """
    # Pozycje zamówienia z SubiektGT
    order_query = """
        SELECT
            p.obDokPoz_Id       AS pozycja_id,
            t.tw_Id             AS towar_id,
            t.tw_Symbol         AS towar_symbol,
            t.tw_Nazwa          AS towar_nazwa,
            t.tw_Ean            AS ean,
            p.obDokPoz_Ilosc    AS ilosc_wymagana
        FROM dok__PozycjaDokumentu p
        JOIN tw__Towar t ON t.tw_Id = p.obDokPoz_TowarId
        WHERE p.obDokPoz_DokumentId = :order_id
        ORDER BY p.obDokPoz_Lp
    """

    with subiekt_engine.connect() as conn:
        df_items = pd.read_sql(text(order_query), conn, params={"order_id": order_id})

    if df_items.empty:
        return []

    # Lokalizacje z bazy analitycznej
    towar_ids = df_items["towar_id"].tolist()
    loc_query = text("""
        SELECT towar_id, lokalizacja_kod, lokalizacja_x, lokalizacja_y
        FROM wms_towar_lokalizacja
        WHERE towar_id = ANY(:ids) AND aktywne = true AND jest_domyslna = true
    """)

    with analytics_engine.connect() as conn:
        df_loc = pd.read_sql(loc_query, conn, params={"ids": towar_ids})

    loc_map = {
        row["towar_id"]: row
        for _, row in df_loc.iterrows()
    }

    items = []
    for _, row in df_items.iterrows():
        loc = loc_map.get(int(row["towar_id"]))
        items.append(ItemDoPick(
            pozycja_id=int(row["pozycja_id"]),
            towar_id=int(row["towar_id"]),
            towar_symbol=str(row["towar_symbol"]),
            towar_nazwa=str(row["towar_nazwa"]),
            ilosc_wymagana=float(row["ilosc_wymagana"]),
            lokalizacja_kod=loc["lokalizacja_kod"] if loc is not None else "BRAK_LOKALIZACJI",
            lokalizacja_x=float(loc["lokalizacja_x"]) if loc is not None else 9999.0,
            lokalizacja_y=float(loc["lokalizacja_y"]) if loc is not None else 0.0,
            ean=str(row["ean"]) if pd.notna(row.get("ean")) else None,
        ))

    return items


def get_optimized_picklist(order_id: int, algorithm: str = "nearest_neighbor") -> dict:
    """Zwraca zoptymalizowaną listę kompletacyjną dla zamówienia."""
    items = get_order_items_with_locations(order_id)
    if not items:
        return {"error": f"Zamówienie {order_id} nie ma pozycji lub nie istnieje."}

    wynik = optimizer.optimize(items, algorithm=algorithm)
    return serialize_trasa(wynik)


# ── Potwierdzenie zebrania pozycji ─────────────────────────────────────────

def verify_and_confirm_pick(
    sesja_id: int,
    pozycja_id: int,
    scan_code: str,
    ilosc: float,
    operator_login: str
) -> dict:
    """
    Weryfikuje skan kodu kreskowego z pozycją zamówienia.
    Zwraca {ok, message, blad}.
    """
    # Sprawdź EAN w bazie SubiektGT
    verify_query = """
        SELECT t.tw_Ean, t.tw_Symbol, t.tw_Nazwa, p.obDokPoz_Ilosc
        FROM dok__PozycjaDokumentu p
        JOIN tw__Towar t ON t.tw_Id = p.obDokPoz_TowarId
        WHERE p.obDokPoz_Id = :pozycja_id
    """

    with subiekt_engine.connect() as conn:
        df = pd.read_sql(text(verify_query), conn, params={"pozycja_id": pozycja_id})

    if df.empty:
        return {"ok": False, "message": "Nieznana pozycja zamówienia.", "blad": True}

    row = df.iloc[0]
    ean_z_bazy = str(row["tw_Ean"]) if pd.notna(row["tw_Ean"]) else ""

    # Weryfikacja kodu
    if scan_code not in [ean_z_bazy, str(row["tw_Symbol"])]:
        logger.warning(f"Błąd skanu: oczekiwano EAN={ean_z_bazy}, otrzymano {scan_code}")
        return {
            "ok": False,
            "message": f"⚠️ BŁĘDNY TOWAR! Oczekiwano: {row['tw_Nazwa']}. Sprawdź ponownie!",
            "blad": True,
            "oczekiwany_ean": ean_z_bazy,
            "zeskanowany": scan_code
        }

    # Zapisz potwierdzenie
    _save_pick_event(sesja_id, pozycja_id, ilosc, operator_login, blad=False)

    return {
        "ok": True,
        "message": f"✅ Zebrano: {row['tw_Nazwa']} × {ilosc}",
        "blad": False,
        "towar_nazwa": str(row["tw_Nazwa"]),
        "ilosc_zatwierdzona": ilosc
    }


def _save_pick_event(sesja_id: int, pozycja_id: int, ilosc: float, operator: str, blad: bool):
    """Zapisuje zdarzenie kompletacji do bazy analitycznej."""
    with analytics_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO wms_pick_events
              (sesja_id, pozycja_id, ilosc, operator_login, timestamp, blad)
            VALUES
              (:sid, :pid, :ilosc, :op, :ts, :blad)
        """), {
            "sid": sesja_id,
            "pid": pozycja_id,
            "ilosc": ilosc,
            "op": operator,
            "ts": datetime.utcnow(),
            "blad": blad
        })
        conn.commit()


# ── Zamknięcie kompletacji ─────────────────────────────────────────────────

def complete_picking_session(sesja_id: int, zamowienie_id: int, operator_login: str) -> dict:
    """
    Zamyka sesję kompletacji.
    W docelowej implementacji: wystawia WZ w SubiektGT przez sferę COM
    lub bezpośrednio przez INSERT do bazy (wymaga znajomości schematu WZ w GT).
    """
    with analytics_engine.connect() as conn:
        conn.execute(text("""
            UPDATE wms_sesje_kompletacji
            SET status = 'zakonczona',
                data_zakonczenia = :ts
            WHERE id = :sid
        """), {"sid": sesja_id, "ts": datetime.utcnow()})
        conn.commit()

    logger.info(f"Sesja kompletacji {sesja_id} zakończona przez {operator_login}.")

    # TODO: Wystawienie WZ w SubiektGT
    # Opcja A: przez SubiektGT Sfera COM (wymaga Windows + SubiektGT na tym samym PC)
    # Opcja B: bezpośredni INSERT do bazy MS SQL (ryzykowne bez dokumentacji InsERT)
    # Opcja C: ręczne zatwierdzenie w GT (najpewniejsza metoda na start)

    return {
        "ok": True,
        "sesja_id": sesja_id,
        "message": "Kompletacja zakończona. Zatwierdź WZ w SubiektGT.",
        "zamowienie_id": zamowienie_id
    }
