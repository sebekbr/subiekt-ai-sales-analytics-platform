"""
Skrypt konfiguracyjny magazynu.
Uruchom jeden raz po instalacji, aby:
  1. Zdefiniować lokalizacje w magazynie (regały/półki)
  2. Przypisać towary do lokalizacji
  3. Wygenerować kody QR do wydruku

Użycie:
  python scripts/setup_warehouse.py --action create_locations
  python scripts/setup_warehouse.py --action assign_products
  python scripts/setup_warehouse.py --action print_qr --output-dir ./qr_codes
"""
import argparse
import os
import sys
import qrcode
from sqlalchemy import text, create_engine
from dotenv import load_dotenv

load_dotenv()

# Połączenie z bazą analityczną
ANALYTICS_CONN = os.environ.get("ANALYTICS_CONN_STR",
    f"postgresql+psycopg2://{os.environ.get('ANALYTICS_USERNAME','subiekt_app')}:"
    f"{os.environ.get('ANALYTICS_PASSWORD','')}@"
    f"{os.environ.get('ANALYTICS_HOST','localhost')}:"
    f"{os.environ.get('ANALYTICS_PORT','5432')}/"
    f"{os.environ.get('ANALYTICS_DATABASE','subiekt_analytics')}"
)
engine = create_engine(ANALYTICS_CONN)


# ── Definicja układu magazynu ──────────────────────────────────────────────
# DOSTOSUJ do swojego magazynu!
# Format: (rząd, regał, liczba_półek, x_metrów_od_wejścia, y_metrów_od_wejścia)
WAREHOUSE_LAYOUT = [
    # Rząd A — przy ścianie lewej
    ("A", 1, 5, 2.0, 0.0),
    ("A", 2, 5, 2.0, 3.0),
    ("A", 3, 5, 2.0, 6.0),
    ("A", 4, 5, 2.0, 9.0),
    ("A", 5, 5, 2.0, 12.0),

    # Rząd B — środkowy
    ("B", 1, 4, 5.0, 0.0),
    ("B", 2, 4, 5.0, 3.0),
    ("B", 3, 4, 5.0, 6.0),
    ("B", 4, 4, 5.0, 9.0),
    ("B", 5, 4, 5.0, 12.0),

    # Rząd C — przy ścianie prawej
    ("C", 1, 5, 8.0, 0.0),
    ("C", 2, 5, 8.0, 3.0),
    ("C", 3, 5, 8.0, 6.0),
    ("C", 4, 5, 8.0, 9.0),
    ("C", 5, 5, 8.0, 12.0),
]


def create_locations():
    """Tworzy lokalizacje w bazie danych na podstawie WAREHOUSE_LAYOUT."""
    locations = []
    for rzad, regal, n_polek, x, y in WAREHOUSE_LAYOUT:
        for polka in range(1, n_polek + 1):
            kod = f"{rzad}{regal:02d}-{polka:03d}"
            locations.append({
                "kod": kod,
                "opis": f"Rząd {rzad}, Regał {regal}, Półka {polka}",
                "rzad": rzad,
                "regal": regal,
                "polka": polka,
                "pozycja_x": x,
                "pozycja_y": y + (polka - 1) * 0.4,  # 40cm między półkami
                "magazyn_id": 1,
                "aktywna": True
            })

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM wms_lokalizacje"))
        conn.execute(text("""
            INSERT INTO wms_lokalizacje
              (kod, opis, rzad, "regał", polka, pozycja_x, pozycja_y, magazyn_id, aktywna)
            VALUES
              (:kod, :opis, :rzad, :regal, :polka, :pozycja_x, :pozycja_y, :magazyn_id, :aktywna)
        """), locations)
        conn.commit()

    print(f"✅ Utworzono {len(locations)} lokalizacji w magazynie.")
    return locations


def assign_products_from_csv(csv_path: str):
    """
    Przypisuje towary do lokalizacji z pliku CSV.
    Format CSV: towar_symbol,towar_id,lokalizacja_kod
    """
    import csv
    rows = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    with engine.connect() as conn:
        for row in rows:
            loc = conn.execute(
                text("SELECT pozycja_x, pozycja_y FROM wms_lokalizacje WHERE kod = :kod"),
                {"kod": row["lokalizacja_kod"]}
            ).fetchone()

            if not loc:
                print(f"  ⚠️ Lokalizacja {row['lokalizacja_kod']} nie istnieje — pominięto {row['towar_symbol']}")
                continue

            conn.execute(text("""
                INSERT INTO wms_towar_lokalizacja
                  (towar_id, towar_symbol, lokalizacja_kod, lokalizacja_x, lokalizacja_y, jest_domyslna, aktywne)
                VALUES (:tid, :sym, :lok, :x, :y, true, true)
                ON CONFLICT (towar_id, lokalizacja_kod) DO UPDATE
                  SET lokalizacja_x = :x, lokalizacja_y = :y, aktywne = true
            """), {
                "tid": int(row["towar_id"]),
                "sym": row["towar_symbol"],
                "lok": row["lokalizacja_kod"],
                "x": float(loc[0]),
                "y": float(loc[1])
            })
        conn.commit()

    print(f"✅ Przypisano {len(rows)} towarów do lokalizacji.")


def generate_qr_codes(output_dir: str = "./qr_codes"):
    """Generuje kody QR dla wszystkich lokalizacji do wydruku."""
    os.makedirs(output_dir, exist_ok=True)

    with engine.connect() as conn:
        import pandas as pd
        df = pd.read_sql(text("SELECT kod, opis FROM wms_lokalizacje WHERE aktywna = true"), conn)

    print(f"Generuję {len(df)} kodów QR do {output_dir}/...")

    for _, row in df.iterrows():
        kod = row["kod"]
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(kod)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(os.path.join(output_dir, f"{kod}.png"))

    print(f"✅ Wygenerowano {len(df)} kodów QR → {output_dir}/")
    print("   Wydrukuj je i przyklej na regałach/półkach.")


def print_location_list():
    """Wyświetla tabelę wszystkich lokalizacji."""
    with engine.connect() as conn:
        import pandas as pd
        df = pd.read_sql(
            text("SELECT kod, opis, pozycja_x, pozycja_y FROM wms_lokalizacje ORDER BY kod"),
            conn
        )
    print(df.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Konfiguracja magazynu WMS")
    parser.add_argument("--action", choices=[
        "create_locations", "assign_products", "print_qr", "list"
    ], required=True)
    parser.add_argument("--csv", help="Ścieżka do CSV z przypisaniem towarów", default="products_locations.csv")
    parser.add_argument("--output-dir", help="Katalog na kody QR", default="./qr_codes")
    args = parser.parse_args()

    if args.action == "create_locations":
        create_locations()
    elif args.action == "assign_products":
        assign_products_from_csv(args.csv)
    elif args.action == "print_qr":
        generate_qr_codes(args.output_dir)
    elif args.action == "list":
        print_location_list()
