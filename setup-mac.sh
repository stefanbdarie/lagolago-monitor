#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  Lago Lago Monitor — Mac/Linux setup script
#  Voer uit met: bash setup-mac.sh
# ─────────────────────────────────────────────────────────
set -e

MONITOR_DIR="$HOME/lago-monitor"
BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${BLUE}▶  $1${NC}"; }
success() { echo -e "${GREEN}✅ $1${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $1${NC}"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   🎟️  Lago Lago Monitor — Mac setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python ──────────────────────────────────────────
info "Python controleren..."
if ! command -v python3 &>/dev/null; then
    warn "Python niet gevonden. Installeer via: https://www.python.org/downloads/"
    exit 1
fi
PY_VERSION=$(python3 --version 2>&1)
success "Python gevonden: $PY_VERSION"

# ── 2. Map aanmaken ────────────────────────────────────
info "Map aanmaken: $MONITOR_DIR"
mkdir -p "$MONITOR_DIR"
success "Map klaar"

# ── 3. Script downloaden van GitHub ───────────────────
info "monitor.py downloaden van GitHub..."
curl -fsSL \
  "https://raw.githubusercontent.com/stefanbdarie/lagolago-monitor/main/monitor.py" \
  -o "$MONITOR_DIR/monitor.py"
success "monitor.py gedownload"

# ── 4. Dependencies installeren ───────────────────────
info "Python packages installeren (requests, beautifulsoup4, playwright)..."
pip3 install --quiet requests beautifulsoup4 lxml playwright
success "Packages geïnstalleerd"

info "Playwright browser (Chromium) installeren..."
python3 -m playwright install chromium
success "Chromium geïnstalleerd"

# ── 5. .env aanmaken als die nog niet bestaat ─────────
ENV_FILE="$MONITOR_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    info ".env bestand aanmaken..."
    cat > "$ENV_FILE" << 'ENVEOF'
# ── Lago Lago Monitor configuratie ──────────────────────
# Vul hier je Gmail App Password in (uit myaccount.google.com/apppasswords)
EMAIL_APP_PASSWORD=VULGINHIER

EMAIL_FROM=spjwinter@gmail.com
EMAIL_TO=spjwinter@gmail.com

# Drempelwaarden (optioneel aanpassen)
PRICE_HIGH_ALERT=300
LOW_STOCK_ALERT=30
PRICE_STEP=10

# TicketSwap event URL (2026)
TICKETSWAP_URL=https://www.ticketswap.nl/event/lago-lago-2026/0a8c9317-1528-467a-8d0e-b048a6bd099b
ENVEOF
    success ".env aangemaakt in $MONITOR_DIR/.env"
    warn "Open $MONITOR_DIR/.env en vul je Gmail App Password in!"
else
    success ".env bestaat al"
fi

# ── 6. run.sh aanmaken ────────────────────────────────
RUN_SH="$MONITOR_DIR/run.sh"
cat > "$RUN_SH" << RUNEOF
#!/usr/bin/env bash
# Laadt .env en draait de monitor
set -a
source "$MONITOR_DIR/.env"
set +a
cd "$MONITOR_DIR"
python3 monitor.py
RUNEOF
chmod +x "$RUN_SH"
success "run.sh aangemaakt"

# ── 7. Cron instellen (7,22,37,52 elk uur) ────────────
info "Cron job instellen (elke 15 minuten)..."

CRON_LOG="$MONITOR_DIR/monitor.log"
CRON_ENTRY="7,22,37,52 * * * * $RUN_SH >> $CRON_LOG 2>&1"

# Verwijder oude lago-monitor cron entry als die bestaat
( crontab -l 2>/dev/null | grep -v "lago-monitor\|lagolago" ; echo "# lago-monitor"; echo "$CRON_ENTRY" ) | crontab -
success "Cron job ingesteld"

# ── 8. Test run ────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Setup compleet! 🎉"
echo ""
echo "  📁 Monitor map:  $MONITOR_DIR"
echo "  ⚙️  Configuratie: $ENV_FILE"
echo "  📋 Log bestand:  $CRON_LOG"
echo ""
warn "Vergeet niet: vul je Gmail App Password in in .env"
echo "  Open terminal en typ:"
echo "  nano $MONITOR_DIR/.env"
echo ""
echo "  Test de monitor met:"
echo "  SEND_STATUS_REPORT=true bash $RUN_SH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
