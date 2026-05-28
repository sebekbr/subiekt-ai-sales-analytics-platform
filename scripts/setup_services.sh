#!/bin/bash
# ============================================================
# SubiektGT AI — konfiguracja usług systemd
# Uruchom jako: sudo bash setup_services.sh
# Po tym wszystko startuje automatycznie przy każdym restarcie serwera
# ============================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

APP_DIR="/opt/subiekt_ai"
VENV="$APP_DIR/venv"

# Wykryj rzeczywisty katalog aplikacji jeśli nie /opt
if [ ! -d "$APP_DIR" ]; then
    warn "Nie znaleziono $APP_DIR — szukam aplikacji..."
    APP_DIR=$(find /home /opt /root -name "main.py" -path "*/app/main.py" 2>/dev/null | head -1 | xargs dirname | xargs dirname)
    VENV="$APP_DIR/venv"
    if [ -z "$APP_DIR" ]; then
        err "Nie znaleziono katalogu aplikacji. Podaj ścieżkę ręcznie w zmiennej APP_DIR."
        exit 1
    fi
    log "Znaleziono aplikację w: $APP_DIR"
fi

# Sprawdź czy venv istnieje
if [ ! -f "$VENV/bin/uvicorn" ]; then
    warn "Brak virtual environment. Tworzę..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q
    log "Virtual environment gotowy."
fi

# ── 1. FastAPI (subiekt-ai) ──────────────────────────────────────────────
log "Tworzę usługę systemd: subiekt-ai (FastAPI)..."
cat > /etc/systemd/system/subiekt-ai.service <<EOF
[Unit]
Description=SubiektGT AI — FastAPI + WMS
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=subiekt-ai

[Install]
WantedBy=multi-user.target
EOF

# ── 2. Metabase ──────────────────────────────────────────────────────────
log "Tworzę usługę systemd: metabase (BI Dashboard)..."

# Znajdź metabase.jar
METABASE_JAR=$(find /opt /home /root -name "metabase.jar" 2>/dev/null | head -1)
if [ -z "$METABASE_JAR" ]; then
    warn "Nie znaleziono metabase.jar — pobieranie..."
    mkdir -p /opt/metabase
    wget -q https://downloads.metabase.com/latest/metabase.jar -O /opt/metabase/metabase.jar
    METABASE_JAR="/opt/metabase/metabase.jar"
    log "Metabase pobrana: $METABASE_JAR"
fi
METABASE_DIR=$(dirname "$METABASE_JAR")

# Znajdź Javę
JAVA_BIN=$(which java 2>/dev/null || echo "")
if [ -z "$JAVA_BIN" ]; then
    warn "Java nie znaleziona — instalacja..."
    apt-get install -y -qq default-jre
    JAVA_BIN=$(which java)
fi
log "Java: $JAVA_BIN ($($JAVA_BIN -version 2>&1 | head -1))"

# Pobierz dane DB z .env jeśli istnieje
DB_USER="metabase"
DB_PASS="zaq1@WSX"
DB_NAME="subiekt_analytics"
DB_HOST="localhost"
DB_PORT="5432"
if [ -f "$APP_DIR/.env" ]; then
    DB_USER=$(grep ANALYTICS_USERNAME "$APP_DIR/.env" | cut -d= -f2 | tr -d ' ')
    DB_PASS=$(grep ANALYTICS_PASSWORD "$APP_DIR/.env" | cut -d= -f2 | tr -d ' ')
    DB_NAME=$(grep ANALYTICS_DATABASE "$APP_DIR/.env" | cut -d= -f2 | tr -d ' ')
    DB_HOST=$(grep ANALYTICS_HOST "$APP_DIR/.env" | cut -d= -f2 | tr -d ' ')
    DB_PORT=$(grep ANALYTICS_PORT "$APP_DIR/.env" | cut -d= -f2 | tr -d ' ')
fi

cat > /etc/systemd/system/metabase.service <<EOF
[Unit]
Description=Metabase BI Dashboard
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=$METABASE_DIR
ExecStart=$JAVA_BIN -Xmx2g -jar $METABASE_JAR
Environment="MB_DB_TYPE=postgres"
Environment="MB_DB_DBNAME=${DB_NAME:-subiekt_analytics}"
Environment="MB_DB_PORT=${DB_PORT:-5432}"
Environment="MB_DB_USER=${DB_USER:-metabase}"
Environment="MB_DB_PASS=${DB_PASS:-zaq1@WSX}"
Environment="MB_DB_HOST=${DB_HOST:-localhost}"
Environment="MB_JETTY_PORT=3000"
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=metabase

[Install]
WantedBy=multi-user.target
EOF

# ── 3. Ollama ────────────────────────────────────────────────────────────
log "Sprawdzam usługę Ollama..."
if ! systemctl list-unit-files | grep -q ollama; then
    warn "Ollama nie zainstalowana jako usługa. Instalacja..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

# ── 4. Nginx ─────────────────────────────────────────────────────────────
log "Konfiguruję Nginx jako reverse proxy..."

# Wykryj IP serwera
SERVER_IP=$(hostname -I | awk '{print $1}')

cat > /etc/nginx/sites-available/subiekt-ai <<EOF
server {
    listen 80 default_server;
    server_name _;

    # Zwiększ timeout dla długich zapytań AI
    proxy_read_timeout 120s;
    proxy_connect_timeout 10s;

    # FastAPI — główne API i aplikacja WMS
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    # Metabase BI — dostępny pod /bi/
    location /bi/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

# Aktywuj konfigurację
ln -sf /etc/nginx/sites-available/subiekt-ai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && log "Konfiguracja Nginx poprawna."

# ── 5. Upewnij się że Redis i PostgreSQL działają ─────────────────────────
log "Sprawdzam Redis i PostgreSQL..."
systemctl enable redis-server postgresql 2>/dev/null || true
systemctl start redis-server postgresql 2>/dev/null || true

# ── 6. Załaduj i uruchom wszystko ────────────────────────────────────────
log "Przeładowanie systemd i uruchomienie wszystkich usług..."
systemctl daemon-reload

# Włącz autostart
systemctl enable subiekt-ai metabase ollama nginx

# Uruchom w odpowiedniej kolejności
systemctl restart redis-server
systemctl restart postgresql
sleep 2

systemctl restart ollama
sleep 2

systemctl restart subiekt-ai
sleep 3

systemctl restart metabase
sleep 2

systemctl reload nginx

# ── 7. Sprawdzenie statusu ────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
echo "  Status usług"
echo "══════════════════════════════════════════════"

check_service() {
    local name=$1
    local port=$2
    if systemctl is-active --quiet "$name"; then
        echo -e "  ${GREEN}✓${NC} $name — działa"
    else
        echo -e "  ${RED}✗${NC} $name — PROBLEM (sprawdź: journalctl -u $name -n 20)"
    fi
}

check_service "postgresql"
check_service "redis-server"
check_service "ollama"
check_service "subiekt-ai"
check_service "metabase"
check_service "nginx"

echo ""
echo "══════════════════════════════════════════════"
echo -e "  ${GREEN}Gotowe!${NC} Adresy dostępu:"
echo "══════════════════════════════════════════════"
echo "  API + Swagger:    http://$SERVER_IP/docs"
echo "  Aplikacja WMS:    http://$SERVER_IP/wms"
echo "  Metabase BI:      http://$SERVER_IP/bi/"
echo "  Health check:     http://$SERVER_IP/health"
echo ""
echo "  Logi na żywo:"
echo "    journalctl -u subiekt-ai -f"
echo "    journalctl -u metabase -f"
echo ""
echo "  Restart wszystkiego po problemie:"
echo "    sudo systemctl restart subiekt-ai metabase"
echo ""
