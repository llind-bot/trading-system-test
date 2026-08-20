#!/usr/bin/env bash
# Trading Dashboard Publish Script
# Usage: ./publish.sh [full|frontend|backend|dev]
set -euo pipefail

REPO="$HOME/trading-system-repo"
DASHBOARD="$REPO/dashboard"
FRONTEND="$DASHBOARD/frontend"
PORT=8081

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1" >&2; }

# ---- Check if dashboard is running ----
dashboard_running() {
    pgrep -f "uvicorn.*dashboard.server" > /dev/null 2>&1
}

# ---- Stop dashboard ----
stop_dashboard() {
    if dashboard_running; then
        log "Stopping dashboard..."
        pkill -f "uvicorn.*dashboard.server" || true
        sleep 1
    fi
}

# ---- Restart dashboard (backend only) ----
restart_dashboard() {
    stop_dashboard
    cd "$REPO"
    nohup python3 -m uvicorn dashboard.server:app \
        --host 0.0.0.0 --port "$PORT" \
        --reload > "$REPO/logs/dashboard.out" 2>&1 &
    log "Dashboard restarted on port $PORT"
    sleep 2
    curl -sf http://localhost:$PORT/api/engine/status >/dev/null && \
        log "Dashboard is responding" || \
        warn "Dashboard started but health check pending"
}

# ---- Build frontend ----
build_frontend() {
    log "Building frontend (Vite)..."
    cd "$FRONTEND"
    npm run build
    log "Frontend built to dist/"
}

# ---- Full deploy ----
do_full() {
    log "=== FULL DEPLOY ==="
    build_frontend
    restart_dashboard
    log "=== DONE ==="
}

# ---- Frontend only (build + reload) ----
do_frontend() {
    log "=== FRONTEND ONLY ==="
    build_frontend
    restart_dashboard
    log "=== DONE ==="
}

# ---- Backend only (restart server) ----
do_backend() {
    log "=== BACKEND ONLY ==="
    restart_dashboard
    log "=== DONE ==="
}

# ---- Dev mode — Vite HMR + uvicorn --reload, no rebuild needed ----
start_dev() {
    log "=== DEV MODE (live) ==="
    
    # Stop prod dashboard first
    stop_dashboard
    
    # Start backend with hot reload (python changes picked up automatically)
    cd "$REPO"
    nohup python3 -m uvicorn dashboard.server:app \
        --host 0.0.0.0 --port "$PORT" \
        --reload > "$REPO/logs/dashboard.out" 2>&1 &
    log "Backend on port $PORT (--reload active)"
    sleep 2
    
    # Start Vite dev server for frontend HMR on port 3000
    cd "$FRONTEND"
    nohup npx vite --host 0.0.0.0 --port 3000 > "$REPO/logs/vite.out" 2>&1 &
    log "Vite dev server on http://localhost:3000 (HMR active)"
    
    log "=== DEV MODE READY ==="
    log "Edit any file → changes auto-reload in browser."
    log "Frontend: http://localhost:3000"
}

# ---- Main ----
case "${1:-full}" in
    full)       do_full ;;
    frontend)   do_frontend ;;
    backend)    do_backend ;;
    dev)        start_dev ;;
    --help|-h)
        echo "Usage: $0 [full|frontend|backend|dev]"
        echo ""
        echo "  full       - Build frontend + restart backend (full deploy)"
        echo "  frontend   - Build frontend + restart backend (for UI changes)"
        echo "  backend    - Restart backend only (for API/code changes, no rebuild)"
        echo "  dev        - Start Vite HMR + uvicorn --reload for live dev mode"
        ;;
    *)
        err "Unknown mode: $1"
        err "Use --help for usage"
        exit 1
        ;;
esac
