"""
Modele SQLAlchemy — tabele bazy analitycznej PostgreSQL.
Tabele te są wypełniane przez ETL z SubiektGT i służą wyłącznie do analiz BI.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, Text, Index
from sqlalchemy.orm import DeclarativeBase


class AnalyticsBase(DeclarativeBase):
    pass


# ── Historia sprzedaży (agregat z FA + WZ) ──────────────────────────────────

class SprzedazHistoria(AnalyticsBase):
    """Denormalizowana historia sprzedaży — podstawa dla analiz BI."""
    __tablename__ = "sprzedaz_historia"

    id = Column(Integer, primary_key=True, autoincrement=True)
    numer_dokumentu = Column(String(50), index=True)
    typ_dokumentu = Column(String(10))              # FA, WZ
    data_wystawienia = Column(DateTime, index=True)
    data_sprzedazy = Column(DateTime)
    rok = Column(Integer, index=True)
    miesiac = Column(Integer, index=True)
    dzien_tygodnia = Column(Integer)                # 0=pon, 6=nd
    kontrahent_id = Column(Integer, index=True)
    kontrahent_symbol = Column(String(50))
    kontrahent_nazwa = Column(String(256))
    towar_id = Column(Integer, index=True)
    towar_symbol = Column(String(50), index=True)
    towar_nazwa = Column(String(256))
    ilosc = Column(Numeric(18, 4))
    cena_netto = Column(Numeric(18, 4))
    wartosc_netto = Column(Numeric(18, 4))
    wartosc_brutto = Column(Numeric(18, 4))
    cena_zakupu = Column(Numeric(18, 4))
    koszt = Column(Numeric(18, 4))                  # cena_zakupu × ilosc
    marza_netto = Column(Numeric(18, 4))            # wartosc_netto - koszt
    marza_procent = Column(Numeric(7, 4))           # marza_netto / wartosc_netto * 100
    magazyn_id = Column(Integer)
    operator_id = Column(Integer)
    etl_timestamp = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_sprzedaz_data_towar", "data_wystawienia", "towar_id"),
        Index("ix_sprzedaz_data_kontrahent", "data_wystawienia", "kontrahent_id"),
    )


# ── Stany magazynowe (snapshot dzienny) ────────────────────────────────────

class StanMagazynowySnapshot(AnalyticsBase):
    """Dzienne snapshoty stanów magazynowych — do analizy trendów i rotacji."""
    __tablename__ = "stany_magazynowe"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_snapshotu = Column(DateTime, index=True)
    magazyn_id = Column(Integer)
    towar_id = Column(Integer, index=True)
    towar_symbol = Column(String(50))
    towar_nazwa = Column(String(256))
    stan = Column(Numeric(18, 4))
    zarezerwowane = Column(Numeric(18, 4))
    dostepne = Column(Numeric(18, 4))
    wartosc_zakupu = Column(Numeric(18, 4))         # stan × cena_zakupu
    etl_timestamp = Column(DateTime, default=datetime.utcnow)


# ── Rotacja towarów ─────────────────────────────────────────────────────────

class RotacjaTowarow(AnalyticsBase):
    """Wskaźniki rotacji — przeliczane co tydzień przez ETL."""
    __tablename__ = "rotacja_towarow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    data_obliczenia = Column(DateTime, index=True)
    towar_id = Column(Integer, index=True)
    towar_symbol = Column(String(50))
    towar_nazwa = Column(String(256))
    magazyn_id = Column(Integer)
    sprzedaz_30d = Column(Numeric(18, 4))           # łączna sprzedaż ostatnie 30 dni
    sprzedaz_90d = Column(Numeric(18, 4))
    sprzedaz_365d = Column(Numeric(18, 4))
    srednia_dzienna = Column(Numeric(18, 4))        # śr. dzienna z ostatnich 90 dni
    stan_biezacy = Column(Numeric(18, 4))
    rotacja_dni = Column(Numeric(10, 2))            # stan / śr. dzienna → ile dni zapasu
    etl_timestamp = Column(DateTime, default=datetime.utcnow)


# ── Lokalizacje WMS ────────────────────────────────────────────────────────

class LokalizacjaWMS(AnalyticsBase):
    """Mapa magazynu — lokalizacje regałów z koordynatami do optymalizacji tras."""
    __tablename__ = "wms_lokalizacje"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kod = Column(String(30), unique=True, nullable=False, index=True)  # np. A01-002-003
    opis = Column(String(200))
    rzad = Column(String(5))                        # A, B, C...
    regał = Column(Integer)                         # 1, 2, 3...
    polka = Column(Integer)                         # 1, 2, 3...
    pozycja_x = Column(Numeric(8, 2))              # koordynat X w metrach od wejścia
    pozycja_y = Column(Numeric(8, 2))              # koordynat Y w metrach od wejścia
    magazyn_id = Column(Integer, default=1)
    aktywna = Column(Boolean, default=True)
    max_ilosc = Column(Numeric(10, 2))             # opcjonalnie: pojemność lokalizacji
    uwagi = Column(Text)


# ── Przypisanie towarów do lokalizacji ────────────────────────────────────

class TowarLokalizacja(AnalyticsBase):
    """Przypisanie towarów do lokalizacji w magazynie."""
    __tablename__ = "wms_towar_lokalizacja"

    id = Column(Integer, primary_key=True, autoincrement=True)
    towar_id = Column(Integer, index=True)
    towar_symbol = Column(String(50))
    lokalizacja_kod = Column(String(30), index=True)
    lokalizacja_x = Column(Numeric(8, 2))
    lokalizacja_y = Column(Numeric(8, 2))
    jest_domyslna = Column(Boolean, default=True)   # główna lokalizacja towaru
    aktywne = Column(Boolean, default=True)


# ── Sesje kompletacji WMS ──────────────────────────────────────────────────

class SesjKompletacji(AnalyticsBase):
    """Historia sesji kompletacji — do analizy wydajności magazynierów."""
    __tablename__ = "wms_sesje_kompletacji"

    id = Column(Integer, primary_key=True, autoincrement=True)
    zamowienie_id = Column(Integer, index=True)     # ID dokumentu ZO z SubiektGT
    zamowienie_numer = Column(String(50))
    operator_id = Column(Integer)
    operator_login = Column(String(50))
    data_rozpoczecia = Column(DateTime)
    data_zakonczenia = Column(DateTime)
    liczba_pozycji = Column(Integer)
    liczba_potwierdzonych = Column(Integer)
    liczba_bledow = Column(Integer, default=0)
    czas_sekund = Column(Integer)                   # łączny czas kompletacji
    wz_numer = Column(String(50))                   # numer WZ wystawionego w GT
    status = Column(String(20), default="w_toku")   # w_toku, zakonczona, porzucona


# ── Operatorzy (użytkownicy WMS) ──────────────────────────────────────────

class OperatorWMS(AnalyticsBase):
    """Konta użytkowników aplikacji WMS (magazynierzy, kierownicy)."""
    __tablename__ = "wms_operatorzy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    login = Column(String(50), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    imie_nazwisko = Column(String(100))
    rola = Column(String(20), default="magazynier") # magazynier, kierownik, admin
    aktywny = Column(Boolean, default=True)
    utworzony = Column(DateTime, default=datetime.utcnow)
    ostatnie_logowanie = Column(DateTime)
