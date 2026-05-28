"""
Modele SQLAlchemy — tabele bazy SubiektGT (MS SQL Server).
UWAGA: Nazwy kolumn mogą się nieznacznie różnić między wersjami SubiektGT.
       Zweryfikuj w SQL Server Management Studio (SSMS) przed uruchomieniem.

Narzędzie do weryfikacji:
  SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
  FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_NAME IN ('dok__Dokument', 'dok__PozycjaDokumentu', 'tw__Towar', 'mag__Stan')
  ORDER BY TABLE_NAME, ORDINAL_POSITION;
"""
from sqlalchemy import Column, Integer, String, Numeric, DateTime, Boolean, ForeignKey, BigInteger
from sqlalchemy.orm import DeclarativeBase, relationship


class SubiektBase(DeclarativeBase):
    pass


# ── Towary ──────────────────────────────────────────────────────────────────

class Towar(SubiektBase):
    """Kartoteka towarów."""
    __tablename__ = "tw__Towar"

    tw_Id = Column(BigInteger, primary_key=True)
    tw_Symbol = Column(String(50), nullable=False, index=True)
    tw_Nazwa = Column(String(256), nullable=False)
    tw_JednostkaMiary = Column(String(10))
    tw_Ean = Column(String(30), index=True)          # główny kod EAN/kreskowy
    tw_TypTowaru = Column(Integer)                    # 1=towar, 2=usługa, 3=komplet
    tw_Zablokowany = Column(Boolean, default=False)
    tw_CenaZakupu = Column(Numeric(18, 4))
    tw_CenaNetto1 = Column(Numeric(18, 4))           # cena sprzedaży netto
    tw_StawkaVat = Column(Numeric(5, 2))
    tw_Masa = Column(Numeric(10, 4))                 # masa [kg]
    tw_Opis = Column(String(4000))

    stany = relationship("StanMagazynowy", back_populates="towar")
    pozycje = relationship("PozycjaDokumentu", back_populates="towar")


# ── Magazyn — stany ─────────────────────────────────────────────────────────

class StanMagazynowy(SubiektBase):
    """Stany magazynowe per towar per magazyn."""
    __tablename__ = "mag__Stan"

    ms_Id = Column(BigInteger, primary_key=True)
    ms_TowarId = Column(BigInteger, ForeignKey("tw__Towar.tw_Id"), index=True)
    ms_MagazynId = Column(Integer, index=True)
    ms_Ilosc = Column(Numeric(18, 4), default=0)     # stan bieżący
    ms_IloscZarezerwowana = Column(Numeric(18, 4), default=0)  # zarezerwowane przez ZO
    ms_IloscZamowiona = Column(Numeric(18, 4), default=0)      # zamówione u dostawcy

    towar = relationship("Towar", back_populates="stany")

    @property
    def dostepny(self) -> float:
        """Ilość dostępna = stan - zarezerwowane."""
        return float(self.ms_Ilosc or 0) - float(self.ms_IloscZarezerwowana or 0)


# ── Magazyn — ruchy ─────────────────────────────────────────────────────────

class RuchMagazynowy(SubiektBase):
    """Historia ruchów magazynowych."""
    __tablename__ = "mag__Ruch"

    mr_Id = Column(BigInteger, primary_key=True)
    mr_TowarId = Column(BigInteger, ForeignKey("tw__Towar.tw_Id"), index=True)
    mr_MagazynId = Column(Integer)
    mr_DokumentId = Column(BigInteger, index=True)
    mr_Data = Column(DateTime, index=True)
    mr_Ilosc = Column(Numeric(18, 4))                # dodatnia = przychód, ujemna = rozchód
    mr_Wartosc = Column(Numeric(18, 4))


# ── Kontrahenci ─────────────────────────────────────────────────────────────

class Kontrahent(SubiektBase):
    """Kartoteka kontrahentów (klienci i dostawcy)."""
    __tablename__ = "kh__Kontrahent"

    kh_Id = Column(BigInteger, primary_key=True)
    kh_Symbol = Column(String(50), index=True)
    kh_Nazwa = Column(String(256), nullable=False)
    kh_NazwaSkrocona = Column(String(50))
    kh_Nip = Column(String(20))
    kh_Miasto = Column(String(100))
    kh_KodPocztowy = Column(String(10))
    kh_Ulica = Column(String(200))
    kh_Telefon = Column(String(50))
    kh_Email = Column(String(200))
    kh_TypKontrahenta = Column(Integer)              # 1=klient, 2=dostawca, 3=oba
    kh_Zablokowany = Column(Boolean, default=False)
    kh_TerminPlatnosci = Column(Integer)             # dni

    dokumenty = relationship("Dokument", back_populates="kontrahent")


# ── Dokumenty ───────────────────────────────────────────────────────────────

class Dokument(SubiektBase):
    """Dokumenty handlowe (FA, WZ, PZ, ZO, ZK, KOR itp.)."""
    __tablename__ = "dok__Dokument"

    dok_Id = Column(BigInteger, primary_key=True)
    dok_NumerPelny = Column(String(50), index=True)
    dok_TypDokumentuSymbol = Column(String(10), index=True)  # FA, WZ, PZ, ZO, ZK...
    dok_DataWystawienia = Column(DateTime, index=True)
    dok_DataSprzedazy = Column(DateTime)
    dok_DataPlatnosci = Column(DateTime)
    dok_KontrahentId = Column(BigInteger, ForeignKey("kh__Kontrahent.kh_Id"), index=True)
    dok_MagazynId = Column(Integer)
    dok_WartoscNetto = Column(Numeric(18, 4))
    dok_WartoscBrutto = Column(Numeric(18, 4))
    dok_WartoscVat = Column(Numeric(18, 4))
    dok_Zaplacono = Column(Numeric(18, 4))
    dok_Stan = Column(Integer)                       # 0=bufor, 1=zatwierdzone, 2=anulowane
    dok_Uwagi = Column(String(4000))
    dok_OperatorId = Column(Integer)

    kontrahent = relationship("Kontrahent", back_populates="dokumenty")
    pozycje = relationship("PozycjaDokumentu", back_populates="dokument")


# ── Pozycje dokumentów ──────────────────────────────────────────────────────

class PozycjaDokumentu(SubiektBase):
    """Pozycje (linie) dokumentów handlowych."""
    __tablename__ = "dok__PozycjaDokumentu"

    obDokPoz_Id = Column(BigInteger, primary_key=True)
    obDokPoz_DokumentId = Column(BigInteger, ForeignKey("dok__Dokument.dok_Id"), index=True)
    obDokPoz_TowarId = Column(BigInteger, ForeignKey("tw__Towar.tw_Id"), index=True)
    obDokPoz_Ilosc = Column(Numeric(18, 4))
    obDokPoz_CenaNetto = Column(Numeric(18, 4))
    obDokPoz_CenaBrutto = Column(Numeric(18, 4))
    obDokPoz_CenaZakupu = Column(Numeric(18, 4))     # do obliczania marży
    obDokPoz_WartoscNetto = Column(Numeric(18, 4))
    obDokPoz_WartoscBrutto = Column(Numeric(18, 4))
    obDokPoz_StawkaVat = Column(Numeric(5, 2))
    obDokPoz_Rabat = Column(Numeric(5, 2))
    obDokPoz_Lp = Column(Integer)                    # linia porządkowa

    dokument = relationship("Dokument", back_populates="pozycje")
    towar = relationship("Towar", back_populates="pozycje")

    @property
    def marza_netto(self) -> float:
        """Marża na pozycji = wartość netto - (cena zakupu × ilość)."""
        wartosc = float(self.obDokPoz_WartoscNetto or 0)
        koszt = float(self.obDokPoz_CenaZakupu or 0) * float(self.obDokPoz_Ilosc or 0)
        return wartosc - koszt
