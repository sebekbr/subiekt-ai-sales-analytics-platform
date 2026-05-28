#!/bin/bash
# ============================================================
# SubiektGT AI Integration — skrypt instalacyjny
# Testowany na Ubuntu Server 22.04 LTS
# Uruchom jako: sudo bash install.sh
# ============================================================

set -e   # przerwij przy błędzie
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo "============================================"
echo "  SubiektGT AI Integration — Instalacja"
echo "============================================"
echo ""

# ── 1. Aktualizacja systemu ──────────────────────────────────────────────
log "Aktualizacja pakietów systemowych..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Podstawowe narzędzia ──────────────────────────────────────────────
log "Instalacja podstawowych pakietów..."
apt-get install -y -qq \
    curl wget git htop unzip \
    build-essential \
    python3.11 python3.11-venv python3-pip \
    unixodbc unixodbc-dev \
    default-jre \
    nginx redis-server \
    postgresql postgresql-contrib

# ── 3. Sterownik ODBC dla MS SQL Server ──────────────────────────────────
log "Instalacja sterownika ODBC dla MS SQL Server..."
if ! dpkg -l | grep -q msodbcsql18; then
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
    curl -fsSL https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list
    apt-get update -qq
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 mssql-tools18
    log "Sterownik ODBC zainstalowany."
else
    warn "Sterownik ODBC już zainstalowany — pominięto."
fi

# ── 4. Ollama (lokalny LLM) ──────────────────────────────────────────────
log "Instalacja Ollama..."
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
    systemctl enable ollama
    systemctl start ollama
    log "Ollama zainstalowana i uruchomiona."
else
    warn "Ollama już zainstalowana — pominięto."
fi

# Pobierz modele (może trwać kilka minut)
log "Pobieranie modelu llama3 (~5GB — proszę czekać)..."
ollama pull llama3

log "Pobieranie modelu nomic-embed-text (~270MB)..."
ollama pull nomic-embed-text

# ── 5. PostgreSQL — konfiguracja ─────────────────────────────────────────
log "Konfiguracja PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

# Utwórz użytkownika i bazę
sudo -u postgres psql <<EOF
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'subiekt_app') THEN
    CREATE USER subiekt_app WITH PASSWORD 'zmien_to_haslo';
  END IF;
END
\$\$;
CREATE DATABASE IF NOT EXISTS subiekt_analytics OWNER subiekt_app;
GRANT ALL PRIVILEGES ON DATABASE subiekt_analytics TO subiekt_app;
EOF
log "PostgreSQL skonfigurowany. ZMIEŃ hasło subiekt_app w .env!"

# ── 6. Redis ─────────────────────────────────────────────────────────────
log "Uruchamianie Redis..."
systemctl enable redis-server
systemctl start redis-server

# ── 7. Aplikacja Python ──────────────────────────────────────────────────
APP_DIR=/opt/subiekt_ai
log "Kopiowanie aplikacji do $APP_DIR..."
mkdir -p $APP_DIR
cp -r . $APP_DIR/
chown -R www-data:www-data $APP_DIR

log "Tworzenie virtual environment Python..."
python3.11 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate
pip install --upgrade pip -q
pip install -r $APP_DIR/requirements.txt -q
log "Pakiety Python zainstalowane."

# Skopiuj .env.example jako .env
if [ ! -f $APP_DIR/.env ]; then
    cp $APP_DIR/.env.example $APP_DIR/.env
    warn "Skopiowano .env.example → .env. UZUPEŁNIJ dane połączenia z SubiektGT!"
fi

# ── 8. Systemd service ──────────────────────────────────────────────────
log "Tworzenie usługi systemd..."
cat > /etc/systemd/system/subiekt-ai.service <<EOF
[Unit]
Description=SubiektGT AI Integration API
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=www-data
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable subiekt-ai

# ── 9. Nginx reverse proxy ───────────────────────────────────────────────
log "Konfiguracja Nginx..."
cat > /etc/nginx/sites-available/subiekt-ai <<'EOF'
server {
    listen 80;
    server_name _;

    # FastAPI + WMS
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120;   # dla długich zapytań AI
    }

    # Metabase BI Dashboard
    location /metabase/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_set_header Host $host;
    }
}
EOF

ln -sf /etc/nginx/sites-available/subiekt-ai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
log "Nginx skonfigurowany."

# ── 10. Metabase ─────────────────────────────────────────────────────────
log "Pobieranie Metabase (BI Dashboard)..."
mkdir -p /opt/metabase
if [ ! -f /opt/metabase/metabase.jar ]; then
    wget -q https://downloads.metabase.com/latest/metabase.jar -O /opt/metabase/metabase.jar
    log "Metabase pobrana."
fi

cat > /etc/systemd/system/metabase.service <<EOF
[Unit]
Description=Metabase BI Dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/metabase
ExecStart=/usr/bin/java -jar /opt/metabase/metabase.jar
Environment="MB_DB_TYPE=postgres"
Environment="MB_DB_DBNAME=subiekt_analytics"
Environment="MB_DB_PORT=5432"
Environment="MB_DB_USER=subiekt_app"
Environment="MB_DB_PASS=zmien_to_haslo"
Environment="MB_DB_HOST=localhost"
Environment="MB_JETTY_PORT=3000"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable metabase

# ── Podsumowanie ─────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo -e "  ${GREEN}Instalacja zakończona!${NC}"
echo "============================================"
echo ""
echo "  NASTĘPNE KROKI:"
echo ""
echo "  1. Edytuj plik konfiguracyjny:"
echo "     nano $APP_DIR/.env"
echo "     (ustaw dane połączenia z SubiektGT i hasła)"
echo ""
echo "  2. Uruchom aplikację:"
echo "     systemctl start subiekt-ai"
echo "     systemctl start metabase"
echo ""
echo "  3. Pierwsze uruchomienie ETL:"
echo "     curl -X POST http://localhost:8000/api/admin/etl/run"
echo ""
echo "  4. Skonfiguruj magazyn:"
echo "     cd $APP_DIR"
echo "     source venv/bin/activate"
echo "     python scripts/setup_warehouse.py --action create_locations"
echo "     python scripts/setup_warehouse.py --action print_qr"
echo ""
echo "  5. Otwórz:"
echo "     API + WMS:  http://$(hostname -I | awk '{print $1}')"
echo "     API Docs:   http://$(hostname -I | awk '{print $1}')/docs"
echo "     Metabase:   http://$(hostname -I | awk '{print $1}')/metabase"
echo ""
warn "PAMIĘTAJ: Zmień hasło subiekt_app w PostgreSQL i .env!"
echo ""
