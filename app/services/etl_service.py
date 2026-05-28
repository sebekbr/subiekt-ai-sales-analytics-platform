"""
ETL — Ekstrakcja danych z SubiektGT do bazy analitycznej.
Uruchamiany cyklicznie przez scheduler (domyślnie o 2:00 w nocy).
Można też wywołać ręcznie przez endpoint /api/admin/etl/run.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.database import subiekt_engine, analytics_engine

logger = logging.getLogger(__name__)


# ── Pomocnik SQL ────────────────────────────────────────────────────────────

def read_subiekt(query: str, params: dict = None) -> pd.DataFrame:
    """Wykonuje SELECT na SubiektGT i zwraca DataFrame."""
    with subiekt_engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


def write_analytics(df: pd.DataFrame, table: str, if_exists: str = "append"):
    """Zapisuje DataFrame do tabeli analitycznej."""
    with analytics_engine.connect() as conn:
        df.to_sql(table, conn, if_exists=if_exists, index=False, method="multi", chunksize=500)
        conn.commit()


# ── ETL: Sprzedaż ──────────────────────────────────────────────────────────

def etl_sprzedaz(date_from: Optional[str] = None, date_to: Optional[str] = None) -> int:
    """
    Ekstrakcja historii sprzedaży (FA + WZ) z SubiektGT.
    Domyślnie pobiera dane z ostatnich 365 dni.
    Zwraca liczbę wierszy załadowanych.
    """
    if not date_from:
        date_from = (datetime.now() - timedelta(days=settings.etl_history_days)).strftime("%Y%m%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y%m%d")

    logger.info(f"ETL sprzedaż: {date_from} → {date_to}")

    query = """
        SELECT
            d.dok_NrPelny            	AS numer_dokumentu,
            d.dok_Typ			AS typ_dokumentu,
            d.dok_DataWyst       	AS data_wystawienia,
            d.dok_DataMag         	AS data_sprzedazy,
            d.dok_OdbiorcaId            AS kontrahent_id,
            k.kh_Symbol                 AS kontrahent_symbol,
            a.adr_Nazwa                 AS kontrahent_nazwa,
            t.tw_Id                     AS towar_id,
            t.tw_Symbol                 AS towar_symbol,
            t.tw_Nazwa                  AS towar_nazwa,
            p.ob_Ilosc            	AS ilosc,
            p.ob_CenaNetto        	AS cena_netto,
            p.ob_WartNetto     	  	AS wartosc_netto,
            p.ob_WartBrutto    	  	AS wartosc_brutto,
            p.ob_CenaNabycia       	AS cena_zakupu,
            d.dok_MagId             	AS magazyn_id
        FROM dok__Dokument d
        JOIN dok_Pozycja p 	      ON p.ob_DokHanId = d.dok_Id
        JOIN tw__Towar t              ON t.tw_Id = p.ob_TowId
        JOIN kh__Kontrahent k         ON k.kh_Id = d.dok_OdbiorcaId
	JOIN adr__Ewid a	      ON k.kh_Id = a.adr_IdObiektu
        WHERE d.dok_DataWyst BETWEEN CAST(:date_from AS datetime) AND CAST(:date_to AS datetime)
          AND d.dok_Typ IN (2, 11)
          AND d.dok_Status = 1
        ORDER BY d.dok_DataWyst
    """

    df = read_subiekt(query, {"date_from": date_from, "date_to": date_to})

    if df.empty:
        logger.warning("Brak danych sprzedaży do załadowania.")
        return 0

    # ── Transformacje ────────────────────────────────────────────────────
    df["data_wystawienia"] = pd.to_datetime(df["data_wystawienia"])
    df["rok"] = df["data_wystawienia"].dt.year
    df["miesiac"] = df["data_wystawienia"].dt.month
    df["dzien_tygodnia"] = df["data_wystawienia"].dt.dayofweek

    df["ilosc"] = pd.to_numeric(df["ilosc"], errors="coerce").fillna(0)
    df["wartosc_netto"] = pd.to_numeric(df["wartosc_netto"], errors="coerce").fillna(0)
    df["cena_zakupu"] = pd.to_numeric(df["cena_zakupu"], errors="coerce").fillna(0)

    df["koszt"] = df["cena_zakupu"] * df["ilosc"]
    df["marza_netto"] = df["wartosc_netto"] - df["koszt"]
    df["marza_procent"] = df.apply(
        lambda r: (r["marza_netto"] / r["wartosc_netto"] * 100) if r["wartosc_netto"] != 0 else 0,
        axis=1
    )
    df["etl_timestamp"] = datetime.utcnow()

    # ── Zapis (zastąp dane za ten okres) ─────────────────────────────────
    with analytics_engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM sprzedaz_historia WHERE data_wystawienia BETWEEN :df AND :dt"
        ), {"df": date_from, "dt": date_to})
        conn.commit()

    write_analytics(df, "sprzedaz_historia")
    logger.info(f"ETL sprzedaż zakończone: {len(df)} wierszy.")
    return len(df)


# ── ETL: Stany magazynowe ───────────────────────────────────────────────────

def etl_stany_magazynowe() -> int:
    """Snapshot bieżących stanów magazynowych."""
    logger.info("ETL stany magazynowe: start")

    query = """
        SELECT
            ms.st_TowId       AS towar_id,
            t.tw_Symbol         AS towar_symbol,
            t.tw_Nazwa          AS towar_nazwa,
            ms.st_MagId     AS magazyn_id,
            ms.st_Stan         AS stan,
            ms.st_StanRez AS zarezerwowane,
            (ms.st_Stan - ms.st_StanRez) AS dostepne,
            c.tc_CenaNetto0     AS cena_zakupu
        FROM tw_Stan ms
        JOIN tw__Towar t ON t.tw_Id = ms.st_TowId
	JOIN tw_Cena c ON t.tw_Id = c.tc_IdTowar
        WHERE t.tw_Zablokowany = 0
          AND ms.st_Stan != 0
    """

    df = read_subiekt(query)
    if df.empty:
        return 0

    df["wartosc_zakupu"] = pd.to_numeric(df["stan"], errors="coerce") * \
                           pd.to_numeric(df["cena_zakupu"], errors="coerce").fillna(0)
    df["data_snapshotu"] = datetime.utcnow()
    df["etl_timestamp"] = datetime.utcnow()
    df.drop(columns=["cena_zakupu"], inplace=True)

    write_analytics(df, "stany_magazynowe")
    logger.info(f"ETL stany magazynowe: {len(df)} wierszy.")
    return len(df)


# ── ETL: Rotacja towarów ────────────────────────────────────────────────────

def etl_rotacja_towarow() -> int:
    """Oblicza wskaźniki rotacji dla wszystkich aktywnych towarów."""
    logger.info("ETL rotacja towarów: start")

    query = """
        SELECT
            t.tw_Id             AS towar_id,
            t.tw_Symbol         AS towar_symbol,
            t.tw_Nazwa          AS towar_nazwa,
            ms.st_MagId     AS magazyn_id,
            ms.st_Stan         AS stan_biezacy,
            COALESCE(s30.sprzedaz,  0) AS sprzedaz_30d,
            COALESCE(s90.sprzedaz,  0) AS sprzedaz_90d,
            COALESCE(s365.sprzedaz, 0) AS sprzedaz_365d
        FROM tw__Towar t
        JOIN tw_Stan ms ON ms.st_TowId = t.tw_Id
        LEFT JOIN (
            SELECT p.ob_TowId AS towar_id,
                   SUM(p.ob_Ilosc) AS sprzedaz
            FROM dok_Pozycja p
            JOIN dok__Dokument d ON d.dok_Id = p.ob_DokHanId
            WHERE d.dok_Typ IN (2, 11)
              AND d.dok_Status = 1
              AND d.dok_DataWyst >= DATEADD(day, -30, GETDATE())
            GROUP BY p.ob_TowId
        ) s30 ON s30.towar_id = t.tw_Id
        LEFT JOIN (
            SELECT p.ob_TowId AS towar_id,
                   SUM(p.ob_Ilosc) AS sprzedaz
            FROM dok_Pozycja p
            JOIN dok__Dokument d ON d.dok_Id = p.ob_DokHanId
            WHERE d.dok_Typ IN (2, 11)
              AND d.dok_Status = 1
              AND d.dok_DataWyst >= DATEADD(day, -90, GETDATE())
            GROUP BY p.ob_TowId
        ) s90 ON s90.towar_id = t.tw_Id
        LEFT JOIN (
            SELECT p.ob_TowId AS towar_id,
                   SUM(p.ob_Ilosc) AS sprzedaz
            FROM dok_Pozycja p
            JOIN dok__Dokument d ON d.dok_Id = p.ob_DokHanId
            WHERE d.dok_Typ IN (2, 11)
              AND d.dok_Status = 1
              AND d.dok_DataWyst >= DATEADD(day, -365, GETDATE())
            GROUP BY p.ob_TowId
        ) s365 ON s365.towar_id = t.tw_Id
        WHERE t.tw_Zablokowany = 0
    """

    df = read_subiekt(query)
    if df.empty:
        return 0

    df["sprzedaz_90d"] = pd.to_numeric(df["sprzedaz_90d"], errors="coerce").fillna(0)
    df["stan_biezacy"] = pd.to_numeric(df["stan_biezacy"], errors="coerce").fillna(0)

    df["srednia_dzienna"] = df["sprzedaz_90d"] / 90
    df["rotacja_dni"] = df.apply(
        lambda r: round(r["stan_biezacy"] / r["srednia_dzienna"], 1)
        if r["srednia_dzienna"] > 0 else 9999,
        axis=1
    )
    df["data_obliczenia"] = datetime.utcnow()
    df["etl_timestamp"] = datetime.utcnow()

    write_analytics(df, "rotacja_towarow")
    logger.info(f"ETL rotacja: {len(df)} wierszy.")
    return len(df)


# ── Pełny ETL (wszystkie moduły) ────────────────────────────────────────────

def run_full_etl(date_from: str = None, date_to: str = None) -> dict:
    """Uruchamia kompletny ETL. Zwraca podsumowanie."""
    started = datetime.utcnow()
    results = {}

    try:
        results["sprzedaz"] = etl_sprzedaz(date_from, date_to)
    except Exception as e:
        logger.error(f"ETL sprzedaż BŁĄD: {e}")
        results["sprzedaz_error"] = str(e)

    try:
        results["stany"] = etl_stany_magazynowe()
    except Exception as e:
        logger.error(f"ETL stany BŁĄD: {e}")
        results["stany_error"] = str(e)

    try:
        results["rotacja"] = etl_rotacja_towarow()
    except Exception as e:
        logger.error(f"ETL rotacja BŁĄD: {e}")
        results["rotacja_error"] = str(e)

    results["czas_sekund"] = (datetime.utcnow() - started).seconds
    results["timestamp"] = started.isoformat()
    logger.info(f"Pełny ETL zakończony w {results['czas_sekund']}s: {results}")
    return results
