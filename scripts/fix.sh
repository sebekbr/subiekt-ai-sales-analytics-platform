#!/bin/bash
# ============================================================
# fix_final.sh — finalna konfiguracja usług systemd
# Uruchom jako: sudo bash fix_final.sh
# ============================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

APP_DIR="/opt/subiekt_ai"
VENV="$APP_DIR/venv"
JAVA_BIN=$(which java 2>/dev/null || { echo "Brak Javy!"; exit 1; })
METABASE_JAR="/opt/metabase/metabase.jar"

echo ""
echo "============================================"
echo "  SubiektGT — finalna konfiguracja usług"
echo "============================================"
echo ""

# ── KROK 1: Napraw import Ollama w ai_service.py ─────────────────────────
# langchain-ollama 1.0.1 używa ChatOllama, nie OllamaLLM
log "Aktualizuję import Ollama w ai_service.py..."
AI_SERVICE="$APP_DIR/app/services/ai_service.py"

if grep -q "from langchain_community.llms import Ollama" "$AI_SERVICE" 2>/dev/null; then
    sed -i 's/from langchain_community.llms import Ollama/from langchain_ollama import ChatOllama as Ollama/' "$AI_SERVICE"
    log "Zamieniono import: langchain_community → langchain_ollama"
elif grep -q "from langchain_ollama import OllamaLLM" "$AI_SERVICE" 2>/dev/null; then
    sed -i 's/from langchain_ollama import OllamaLLM as Ollama/from langchain_ollama import ChatOllama as Ollama/' "$AI_SERVICE"
    log "Zamieniono OllamaLLM → ChatOllama"
elif grep -q "ChatOllama" "$AI_SERVICE" 2>/dev/null; then
    log "Import Ollama już poprawny."
else
    warn "Nie znaleziono importu Ollama — sprawdź ręcznie $AI_SERVICE"
fi

# ── KROK 2: Sprawdź .env ─────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    warn "Brak .env — kopiuję z .env.example"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    warn "Uzupełnij $APP_DIR/.env przed startem!"
else
    log "Plik .env istnieje."
fi

# ── KROK 3: Usługa Metabase ───────────────────────────────────────────────
log "Konfiguruję usługę metabase..."
cat > /etc/systemd/system/metabase.service <<EOF
[Unit]
Description=Metabase BI Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/metabase
ExecStart=$JAVA_BIN -Xmx2g -jar $METABASE_JAR
Environment="MB_JETTY_PORT=3000"
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=metabase

[Install]
WantedBy=multi-user.target
EOF

# ── KROK 4: Usługa FastAPI ────────────────────────────────────────────────
log "Konfiguruję usługę subiekt-ai (FastAPI)..."
cat > /etc/systemd/system/subiekt-ai.service <<EOF
[Unit]
Description=SubiektGT AI — FastAPI + WMS
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=5
EnvironmentFile=$APP_DIR/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=subiekt-ai

[Install]
WantedBy=multi-user.target
EOF

# ── KROK 5: Nginx ─────────────────────────────────────────────────────────
if command -v nginx &>/dev/null; then
    log "Konfiguruję Nginx..."
    cat > /etc/nginx/sites-available/subiekt-ai <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    proxy_read_timeout 120s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /bi/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_set_header Host $host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX
    ln -sf /etc/nginx/sites-available/subiekt-ai /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
    nginx -t && log "Nginx OK."
fi

# ── KROK 6: Przeładuj i uruchom ───────────────────────────────────────────
log "Przeładowanie systemd..."
systemctl daemon-reload

systemctl enable metabase subiekt-ai nginx 2>/dev/null || true

log "Uruchamiam usługi..."
systemctl restart metabase
sleep 3
systemctl restart subiekt-ai
sleep 3
systemctl reload nginx 2>/dev/null || true

# ── KROK 7: Status ────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
echo "  Status usług"
echo "══════════════════════════════════════════════"

for svc in metabase subiekt-ai nginx; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $svc — działa"
    else
        echo -e "  ${RED}✗${NC} $svc — problem"
        echo "    → logi: journalctl -u $svc -n 30 --no-pager"
    fi
done

SERVER_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "══════════════════════════════════════════════"
echo "  Adresy dostępu:"
echo "══════════════════════════════════════════════"
echo "  FastAPI / WMS:  http://$SERVER_IP/"
echo "  API docs:       http://$SERVER_IP/docs"
echo "  Metabase BI:    http://$SERVER_IP/bi/"
echo ""
echo "  Logi na żywo:"
echo "    journalctl -u subiekt-ai -f"
echo "    journalctl -u metabase -f"
echo ""
