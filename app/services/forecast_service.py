"""
Prognozowanie zapotrzebowania na towary metodami ML.
Używa Ridge Regression z feature engineering (trendy, sezonowość).
Dla towarów z bogatą historią (>90 dni) wyniki są bardzo użyteczne.
"""
import logging
from typing import Optional
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error
from sqlalchemy import text

from app.database import analytics_engine

logger = logging.getLogger(__name__)


def get_sales_history(towar_symbol: str, days: int = 365) -> pd.DataFrame:
    """Pobiera historię sprzedaży produktu z bazy analitycznej."""
    query = text("""
        SELECT DATE(data_wystawienia) AS data, SUM(ilosc) AS sprzedaz
        FROM sprzedaz_historia
        WHERE towar_symbol = :symbol
          AND data_wystawienia >= NOW() - INTERVAL ':days days'
          AND ilosc > 0
        GROUP BY DATE(data_wystawienia)
        ORDER BY data
    """)

    with analytics_engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT DATE(data_wystawienia) AS data, SUM(ilosc) AS sprzedaz
                FROM sprzedaz_historia
                WHERE towar_symbol = :symbol
                  AND data_wystawienia >= CURRENT_DATE - :days
                  AND ilosc > 0
                GROUP BY DATE(data_wystawienia)
                ORDER BY data
            """),
            conn,
            params={"symbol": towar_symbol, "days": days}
        )
    return df


def forecast_demand(
    towar_symbol: str,
    days_ahead: int = 30,
    history_days: int = 365
) -> dict:
    """
    Prognozuje dzienne zapotrzebowanie na produkt.
    
    Algorytm:
      1. Pobiera historię sprzedaży
      2. Uzupełnia brakujące dni zerami (ciągły szereg czasowy)
      3. Feature engineering: trend, dzień tygodnia, miesiąc, lag7, lag30
      4. Trenuje Ridge Regression
      5. Prognozuje na days_ahead dni w przód
    
    Returns:
      dict z prognozą dzienną i podsumowaniem.
    """
    df = get_sales_history(towar_symbol, history_days)

    if df.empty or len(df) < 14:
        return {
            "towar_symbol": towar_symbol,
            "blad": "Zbyt mało danych historycznych (minimum 14 dni sprzedaży).",
            "prognoza_lacznie": 0,
            "dni": []
        }

    # ── Przygotowanie szeregu czasowego ─────────────────────────────────
    df["data"] = pd.to_datetime(df["data"])
    df = df.set_index("data").resample("D").sum().fillna(0).reset_index()

    # ── Feature engineering ──────────────────────────────────────────────
    df["trend"] = range(len(df))
    df["dzien_tygodnia"] = df["data"].dt.dayofweek     # 0=pon
    df["miesiac"] = df["data"].dt.month
    df["tydzien_roku"] = df["data"].dt.isocalendar().week.astype(int)

    # Lagged features (sprzedaż z poprzednich tygodni)
    df["lag_7"] = df["sprzedaz"].shift(7).fillna(0)
    df["lag_14"] = df["sprzedaz"].shift(14).fillna(0)
    df["lag_30"] = df["sprzedaz"].shift(30).fillna(0)
    df["rolling_7"] = df["sprzedaz"].rolling(7, min_periods=1).mean()
    df["rolling_30"] = df["sprzedaz"].rolling(30, min_periods=1).mean()

    feature_cols = ["trend", "dzien_tygodnia", "miesiac", "tydzien_roku",
                    "lag_7", "lag_14", "lag_30", "rolling_7", "rolling_30"]

    # Upewnij się, że mamy pełne dane (bez NaN)
    df_clean = df.dropna(subset=feature_cols)

    X = df_clean[feature_cols].values
    y = df_clean["sprzedaz"].values

    # ── Trening modelu ───────────────────────────────────────────────────
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = Ridge(alpha=1.0)
    model.fit(X_scaled, y)

    # Metryka jakości (MAE na danych treningowych — orientacyjna)
    y_pred_train = model.predict(X_scaled).clip(0)
    mae = float(mean_absolute_error(y, y_pred_train))

    # ── Prognoza ─────────────────────────────────────────────────────────
    last_date = df["data"].max()
    last_trend = int(df["trend"].max())
    last_values = df["sprzedaz"].values

    future_rows = []
    recent_values = list(last_values[-30:])  # okno do lagów

    for i in range(days_ahead):
        future_date = last_date + timedelta(days=i + 1)
        trend_val = last_trend + i + 1

        lag_7_val = recent_values[-7] if len(recent_values) >= 7 else 0
        lag_14_val = recent_values[-14] if len(recent_values) >= 14 else 0
        lag_30_val = recent_values[-30] if len(recent_values) >= 30 else 0
        rolling_7_val = float(np.mean(recent_values[-7:])) if len(recent_values) >= 7 else 0
        rolling_30_val = float(np.mean(recent_values[-30:])) if recent_values else 0

        row = [
            trend_val,
            future_date.dayofweek,
            future_date.month,
            future_date.isocalendar()[1],
            lag_7_val, lag_14_val, lag_30_val,
            rolling_7_val, rolling_30_val
        ]
        future_rows.append((future_date, row))

    X_future = np.array([r[1] for r in future_rows])
    X_future_scaled = scaler.transform(X_future)
    predictions = model.predict(X_future_scaled).clip(0)

    # Aktualizuj okno lagów
    for pred in predictions:
        recent_values.append(float(pred))

    prognoza_dzienna = [
        {
            "data": str(future_rows[i][0].date()),
            "ilosc": round(float(predictions[i]), 2),
            "dzien_tygodnia": future_rows[i][0].strftime("%A")
        }
        for i in range(days_ahead)
    ]

    return {
        "towar_symbol": towar_symbol,
        "dni_prognozy": days_ahead,
        "prognoza_lacznie": round(float(predictions.sum()), 2),
        "srednia_dzienna": round(float(predictions.mean()), 2),
        "mae_modelu": round(mae, 2),
        "historia_dni": len(df),
        "prognoza_dzienna": prognoza_dzienna
    }


def get_reorder_suggestions(min_rotacja_dni: int = 14) -> list:
    """
    Zwraca listę towarów wymagających zamówienia uzupełniającego.
    Kryterium: rotacja_dni < min_rotacja_dni i jest sprzedaż w ostatnich 90 dniach.
    """
    query = text("""
        SELECT
            r.towar_symbol,
            r.towar_nazwa,
            r.stan_biezacy,
            r.srednia_dzienna,
            r.rotacja_dni,
            r.sprzedaz_30d
        FROM rotacja_towarow r
        WHERE r.rotacja_dni < :min_dni
          AND r.rotacja_dni < 9999
          AND r.sprzedaz_30d > 0
          AND r.data_obliczenia = (
              SELECT MAX(data_obliczenia) FROM rotacja_towarow
          )
        ORDER BY r.rotacja_dni ASC
        LIMIT 50
    """)

    with analytics_engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"min_dni": min_rotacja_dni})

    if df.empty:
        return []

    # Szacowana ilość do zamówienia = śr. dzienna × 30 dni - stan bieżący
    df["sugerowana_ilosc_zamowienia"] = (
        (df["srednia_dzienna"] * 30) - df["stan_biezacy"]
    ).clip(0).round(2)

    return df.to_dict(orient="records")
