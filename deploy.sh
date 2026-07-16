#!/bin/bash
# ============================================================================
# Dhaka Exclusive — VPS Auto-Deploy Script
# ============================================================================
# Usage: bash deploy.sh
# Called by: GitHub Actions, GitHub Webhook, or manual SSH
#
# This script:
#   1. Pulls latest code from GitHub
#   2. Installs/updates Python dependencies
#   3. Restarts the app (systemd or supervisor)
#   4. Sends Telegram notification to admin
# ============================================================================

set -e  # Exit on any error

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
APP_DIR="${APP_DIR:-/home/ubuntu/dhaka-bot}"          # Where the app lives on VPS
SERVICE_NAME="${SERVICE_NAME:-dhaka-bot}"             # systemd service name
BRANCH="${BRANCH:-main}"                              # Git branch
GUNICORN_PORT="${PORT:-5000}"                         # App port
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"                # Virtualenv path
MAX_RESTART_WAIT="${MAX_RESTART_WAIT:-30}"            # Seconds to wait for restart

# Telegram notification (optional — set env vars)
TG_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_ADMIN_IDS="${ADMIN_TELEGRAM_IDS:-}"

# ─── COLORS ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; }

# ─── TELEGRAM NOTIFICATION ───────────────────────────────────────────────────
notify_tg() {
    local msg="$1"
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_ADMIN_IDS" ]; then
        for tg_id in ${TG_ADMIN_IDS//,/ }; do
            curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${tg_id}" \
                -d "text=${msg}" \
                -d "parse_mode=Markdown" > /dev/null 2>&1 || true
        done
    fi
}

# ─── MAIN ────────────────────────────────────────────────────────────────────
log "🚀 Starting deploy: $(date '+%Y-%m-%d %H:%M:%S')"

# 1. Navigate to app directory
if [ ! -d "$APP_DIR" ]; then
    log "App directory not found — cloning..."
    git clone https://github.com/printthp/Dhaka-exclusive-sms-bot-wp-msg.git "$APP_DIR"
fi

cd "$APP_DIR"
log "📁 Working directory: $(pwd)"

# 2. Pull latest code
log "⬇️  Pulling latest from $BRANCH..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd
LATEST_COMMIT=$(git log -1 --format="%h — %s")
log "📝 Latest commit: $LATEST_COMMIT"

# 3. Setup virtualenv if not exists
if [ ! -d "$VENV_DIR" ]; then
    log "🐍 Creating virtualenv..."
    python3 -m venv "$VENV_DIR"
fi

# 4. Install dependencies
log "📦 Installing Python dependencies..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install gunicorn -q  # Ensure gunicorn is installed
log "✅ Dependencies installed"

# 5. Create data/media dirs
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/media"
log "📂 Data directories ensured"

# 6. Restart the service
log "🔄 Restarting service: $SERVICE_NAME"

# Check which service manager is available
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    log "   Using systemd..."
    sudo systemctl restart "$SERVICE_NAME"
    
    # Wait for it to come back up
    for i in $(seq 1 $MAX_RESTART_WAIT); do
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log "   ✅ Service is running!"
            break
        fi
        sleep 1
    done
    
    STATUS=$(systemctl is-active "$SERVICE_NAME")
    log "   Service status: $STATUS"
    
elif supervisorctl status "$SERVICE_NAME" &>/dev/null; then
    log "   Using supervisor..."
    supervisorctl restart "$SERVICE_NAME"
    sleep 3
    STATUS=$(supervisorctl status "$SERVICE_NAME")
    log "   Service status: $STATUS"
    
else
    warn "No service manager found (systemd/supervisor). Manual start needed."
    log "   Run: cd $APP_DIR && source .venv/bin/activate && gunicorn -w 2 -b 0.0.0.0:$GUNICORN_PORT app:application --daemon"
fi

# 7. Health check
log "🏥 Running health check..."
sleep 2
if curl -sf http://localhost:$GUNICORN_PORT/api/telegram-status > /dev/null 2>&1; then
    log "✅ Health check PASSED"
    HEALTH="✅ OK"
else
    warn "Health check FAILED (port $GUNICORN_PORT)"
    HEALTH="⚠️ FAILED"
fi

# 8. Send notification
DEPLOY_MSG=$(cat <<EOF
🎉 *Deploy Complete!*
📅 $(date '+%Y-%m-%d %H:%M:%S')
📝 *Commit:* $LATEST_COMMIT
🖥️ *Service:* $SERVICE_NAME
🏥 *Health:* $HEALTH
EOF
)

notify_tg "$DEPLOY_MSG"
log "📨 Notification sent to Telegram"

log "🎯 Deploy finished successfully!"
echo ""
