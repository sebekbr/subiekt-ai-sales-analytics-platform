"""
Serwis AI — integracja z lokalnym modelem LLM przez Ollama.
Obsługuje:
  - Pytania w języku naturalnym (PL) → SQL → odpowiedź PL
  - Analizy predyktywne z interpretacją
  - Cache wyników (Redis) dla częstych pytań
"""
import json
import logging
import hashlib
from typing import Optional

import pandas as pd
import redis
from langchain_ollama import ChatOllama as Ollama
from langchain_core.prompts import PromptTemplate

from sqlalchemy import text

from app.config import settings
from app.database import analytics_engine

logger = logging.getLogger(__name__)

# ── Klient Redis (cache) ────────────────────────────────────────────────────

try:
    cache = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        decode_responses=True
    )
    cache.ping()
    logger.info("Redis cache: OK")
except Exception:
    cache = None
    logger.warning("Redis niedostępny — cache wyłączony.")

# ── Inicjalizacja LLM ───────────────────────────────────────────────────────

llm = Ollama(
    model=settings.ollama_model,
    base_url=settings.ollama_host,
    temperature=0.1,      # niska temperatura = bardziej deterministyczne SQL
    num_predict=1024,
)

# ── Kontekst schematu bazy (wstrzykiwany do promptu) ───────────────────────

SCHEMA_CONTEXT = """
Masz dostęp do bazy analitycznej PostgreSQL z następującymi tabelami:

TABELA: sprzedaz_historia
  - numer_dokumentu (text): numer FA/WZ
  - typ_dokumentu (text): 'FA' lub 'WZ'
  - data_wystawienia (timestamp): data dokumentu
  - rok, miesiac, dzien_tygodnia (integer): pola czasowe
  - kontrahent_id, kontrahent_symbol, kontrahent_nazwa (text): klient
  - towar_id, towar_symbol, towar_nazwa (text): produkt
  - ilosc (numeric): sprzedana ilość
  - cena_netto (numeric): cena jednostkowa netto
  - wartosc_netto, wartosc_brutto (numeric): wartości pozycji
  - koszt (numeric): cena_zakupu × ilosc
  - marza_netto (numeric): wartosc_netto - koszt
  - marza_procent (numeric): procent marży

TABELA: stany_magazynowe
  - data_snapshotu (timestamp): data snapshotu
  - towar_id, towar_symbol, towar_nazwa (text)
  - stan (numeric): bieżący stan
  - zarezerwowane (numeric): zarezerwowane przez zamówienia
  - dostepne (numeric): stan - zarezerwowane
  - wartosc_zakupu (numeric): wartość stanu po cenach zakupu

TABELA: rotacja_towarow
  - data_obliczenia (timestamp)
  - towar_id, towar_symbol, towar_nazwa (text)
  - sprzedaz_30d, sprzedaz_90d, sprzedaz_365d (numeric): sprzedaż w okresach
  - srednia_dzienna (numeric): średnia dzienna z 90 dni
  - stan_biezacy (numeric): aktualny stan
  - rotacja_dni (numeric): ile dni zapasu pozostało (mniejsze = szybciej rotuje)

ZASADY:
1. Generuj TYLKO zapytania SELECT — nigdy INSERT, UPDATE, DELETE, DROP.
2. Używaj składni PostgreSQL.
3. Ogranicz wyniki do max 100 wierszy jeśli pytanie nie wymaga inaczej.
4. Przy analizie "ostatnich X dni" używaj: WHERE data_wystawienia >= NOW() - INTERVAL 'X days'
5. Przy grupowaniu po miesiącach: GROUP BY rok, miesiac ORDER BY rok, miesiac
6. Zawsze używaj ROUND(wartosc, 2) dla kwot i procentów.
"""

# ── SQL generation prompt ───────────────────────────────────────────────────

SQL_PROMPT = PromptTemplate(
    input_variables=["schema", "question"],
    template="""Jesteś ekspertem SQL. Na podstawie schematu bazy danych wygeneruj zapytanie SQL.

{schema}

Pytanie użytkownika: {question}

Zwróć WYŁĄCZNIE poprawne zapytanie SQL bez żadnych wyjaśnień, komentarzy ani backtick'ów.
Zapytanie SQL:"""
)

# ── Answer interpretation prompt ────────────────────────────────────────────

ANSWER_PROMPT = PromptTemplate(
    input_variables=["question", "results", "sql"],
    template="""Jesteś analitykiem biznesowym. Na podstawie wyników zapytania SQL odpowiedz na pytanie.

Pytanie: {question}

Wyniki zapytania:
{results}

Odpowiedz po polsku, zwięźle i biznesowo. Podkreśl najważniejsze wnioski.
Jeśli wyniki są puste, powiedz o tym.
Nie pokazuj kodu SQL w odpowiedzi.

Odpowiedź:"""
)


def _get_cache_key(question: str) -> str:
    return f"ai:query:{hashlib.md5(question.encode()).hexdigest()}"


def _execute_sql_safely(sql: str) -> pd.DataFrame:
    """Wykonuje SQL na bazie analitycznej z zabezpieczeniami."""
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        raise ValueError("Dozwolone są tylko zapytania SELECT.")
    forbidden = ["DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER", "CREATE"]
    for word in forbidden:
        if word in sql_upper:
            raise ValueError(f"Zapytanie zawiera niedozwolone słowo kluczowe: {word}")

    with analytics_engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


async def ai_query(question: str, use_cache: bool = True) -> dict:
    """
    Główna funkcja: pytanie PL → SQL → dane → odpowiedź PL.
    
    Returns:
        {
          "question": str,
          "sql": str,
          "data": list[dict],
          "answer": str,
          "rows_count": int,
          "cached": bool
        }
    """
    cache_key = _get_cache_key(question)

    # Sprawdź cache
    if use_cache and cache:
        cached = cache.get(cache_key)
        if cached:
            result = json.loads(cached)
            result["cached"] = True
            return result

    # Krok 1: Generuj SQL
    sql_chain = SQL_PROMPT | llm
    raw_sql = sql_chain.invoke({"schema": SCHEMA_CONTEXT, "question": question}).content.strip()

    # Wyczyść odpowiedź LLM (usuń ewentualne markdown backticki)
    sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    if ";" in sql:
        sql = sql.split(";")[0].strip()

    logger.info(f"Wygenerowany SQL: {sql}")

    # Krok 2: Wykonaj SQL
    try:
        df = _execute_sql_safely(sql)
    except Exception as e:
        return {
            "question": question,
            "sql": sql,
            "data": [],
            "answer": f"Nie udało się wykonać zapytania: {str(e)}",
            "rows_count": 0,
            "cached": False,
            "error": str(e)
        }

    results_str = df.head(50).to_string(index=False) if not df.empty else "Brak wyników."

    # Krok 3: Interpretacja
    answer_chain = ANSWER_PROMPT | llm
    answer = answer_chain.invoke({"question": question, "results": results_str, "sql": sql}).content

    result = {
        "question": question,
        "sql": sql,
        "data": df.head(100).to_dict(orient="records"),
        "answer": answer,
        "rows_count": len(df),
        "cached": False
    }

    # Zapisz do cache na 30 minut
    if cache:
        cache.setex(cache_key, 1800, json.dumps(result, default=str))

    return result


def get_quick_stats() -> dict:
    """Szybkie statystyki bez LLM — do dashboardu głównego."""
    queries = {
        "sprzedaz_miesiac": """
            SELECT ROUND(SUM(wartosc_netto)::numeric, 2) AS wartosc
            FROM sprzedaz_historia
            WHERE data_wystawienia >= NOW() - INTERVAL '30 days'
        """,
        "zamowienia_dzis": """
            SELECT COUNT(DISTINCT numer_dokumentu) AS liczba
            FROM sprzedaz_historia
            WHERE DATE(data_wystawienia) = CURRENT_DATE
        """,
        "towary_niski_stan": """
            SELECT COUNT(*) AS liczba
            FROM rotacja_towarow
            WHERE rotacja_dni < 14 AND rotacja_dni < 9999
            ORDER BY data_obliczenia DESC
            LIMIT 1
        """,
        "srednia_marza": """
            SELECT ROUND(AVG(marza_procent)::numeric, 1) AS procent
            FROM sprzedaz_historia
            WHERE data_wystawienia >= NOW() - INTERVAL '30 days'
              AND wartosc_netto > 0
        """
    }

    stats = {}
    for key, query in queries.items():
        try:
            with analytics_engine.connect() as conn:
                result = pd.read_sql(text(query), conn)
                stats[key] = result.iloc[0, 0] if not result.empty else None
        except Exception as e:
            stats[key] = None
            logger.warning(f"Quick stats '{key}' błąd: {e}")

    return stats
