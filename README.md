# SubiektGT AI Integration

Kompletna integracja SubiektGT z AI (analizy BI) i WMS (kompletacja zamówień).  
Serwer: Xeon E-2186G | 64 GB RAM | Ubuntu Server 22.04 | Koszt licencji: 0 zł

---

## Struktura projektu

```
subiekt_ai/
├── app/
│   ├── main.py                  # punkt wejścia FastAPI
│   ├── config.py                # konfiguracja (czyta .env)
│   ├── database.py              # połączenia MS SQL + PostgreSQL
│   ├── scheduler.py             # harmonogram ETL (APScheduler)
│   ├── models/
│   │   ├── subiekt.py           # tabele SubiektGT (SQLAlchemy)
│   │   └── analytics.py        # tabele bazy analitycznej
│   ├── routers/
│   │   ├── bi.py               # endpointy BI i AI
│   │   └── wms.py              # endpointy WMS
│   └── services/
│       ├── etl_service.py       # ekstrakcja danych z SubiektGT
│       ├── ai_service.py        # Ollama + LangChain
│       ├── route_optimizer.py   # optymalizacja trasy kompletacji
│       ├── wms_service.py       # logika kompletacji
│       └── forecast_service.py  # ML prognozowanie zapotrzebowania
├── frontend/
│   └── wms/
│       └── index.html          # aplikacja PWA dla magazynierów
├── scripts/
│   ├── install.sh              # instalacja na Ubuntu Server
│   └── setup_warehouse.py      # konfiguracja mapy magazynu
├── tests/
│   └── test_etl.py
├── requirements.txt
└── .env.example
```

---

## Szybka instalacja

```bash
# Sklonuj/skopiuj repo na serwer
cd /opt
git clone <repo_url> subiekt_ai  # lub scp -r ./subiekt_ai user@server:/opt/

# Instalacja (wymaga sudo)
cd /opt/subiekt_ai
sudo bash scripts/install.sh

# Konfiguracja
nano .env   # uzupełnij dane SubiektGT!

# Uruchomienie
systemctl start subiekt-ai
systemctl start metabase

# Pierwsze zasilenie bazy analitycznej
curl -X POST http://localhost:8000/api/admin/etl/run
```

---

## Konfiguracja magazynu (jednorazowo)

```bash
source /opt/subiekt_ai/venv/bin/activate
cd /opt/subiekt_ai

# 1. Dostosuj układ w scripts/setup_warehouse.py (WAREHOUSE_LAYOUT)
# 2. Utwórz lokalizacje w bazie
python scripts/setup_warehouse.py --action create_locations

# 3. Wygeneruj kody QR do wydruku
python scripts/setup_warehouse.py --action print_qr --output-dir ./qr_codes

# 4. Przypisz towary do lokalizacji (z pliku CSV)
# Format CSV: towar_symbol,towar_id,lokalizacja_kod
python scripts/setup_warehouse.py --action assign_products --csv produkty_lokalizacje.csv
```

---

## API — główne endpointy

### BI (analityka)
| Endpoint | Opis |
|---|---|
| `GET /api/bi/dashboard/summary` | KPI główne |
| `GET /api/bi/sales/summary` | Sprzedaż w okresie |
| `GET /api/bi/sales/top-products` | Ranking produktów |
| `GET /api/bi/customers/rfm` | Segmentacja RFM |
| `GET /api/bi/stock/rotation` | Rotacja magazynowa |
| `GET /api/bi/stock/reorder` | Sugestie zamówień |
| `GET /api/bi/forecast/{symbol}` | Prognoza zapotrzebowania |
| `POST /api/bi/ai/query` | Pytanie w języku naturalnym |

### WMS (kompletacja)
| Endpoint | Opis |
|---|---|
| `GET /api/wms/orders/pending` | Zamówienia do kompletacji |
| `GET /api/wms/order/{id}/picklist` | Optymalna lista kompletacyjna |
| `POST /api/wms/session/start` | Start sesji kompletacji |
| `POST /api/wms/picking/confirm` | Potwierdzenie skanu |
| `POST /api/wms/session/complete` | Zamknięcie kompletacji |

Pełna dokumentacja: `http://serwer/docs`

---

## Przykłady zapytań AI

```bash
curl -X POST http://localhost:8000/api/bi/ai/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Które 5 produktów miało największy spadek sprzedaży w ostatnim miesiącu?"}'

curl -X POST http://localhost:8000/api/bi/ai/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Pokaż klientów z marżą poniżej 10% w ostatnim kwartale"}'
```

---

## Dostęp

- **API + WMS:** `http://192.168.1.10/` lub `http://192.168.1.10/docs`
- **Aplikacja mobilna WMS:** `http://192.168.1.10/wms` (otwórz na telefonie)
- **Metabase BI:** `http://192.168.1.10/metabase`

---

## Testy

```bash
source venv/bin/activate
pytest tests/ -v
```

---

## Wymagania systemowe

| Komponent | Wersja |
|---|---|
| Ubuntu Server | 22.04 LTS |
| Python | 3.11+ |
| MS SQL Server (SubiektGT) | 2016+ |
| PostgreSQL | 14+ |
| Java (Metabase) | 11+ |
| RAM wolny dla Ollama | min. 6 GB |
