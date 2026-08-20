"""Trading Dashboard — FastAPI entry point."""
import sys
import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not os.environ.get(key):
                    os.environ[key] = value

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "strategy-lab"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

from dashboard.config.settings import DASHBOARD_HOST, DASHBOARD_PORT
from dashboard.websocket.live_feed import live_feed, _eastern_now_str
from dashboard.api.positions import router as positions_router
from dashboard.api.orders import router as orders_router
from dashboard.api.watchlist_mgmt import router as watchlist_mgmt_router
from dashboard.api.simulation import router as simulation_router
from dashboard.api.strategies import router as strategies_router
from dashboard.api.logs import router as logs_router
from dashboard.api.reports import router as reports_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await live_feed.start()
    print(f"[dashboard] WebSocket live feed started")
    yield
    await live_feed.stop()
    print("[dashboard] WebSocket live feed stopped")


app = FastAPI(
    title="Trading Dashboard",
    description="Interactive trading system dashboard",
    version="0.2.1-fix-pnl",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Timezone conversion: UTC → EDT/EST for all API responses ───────────
app.include_router(positions_router)
app.include_router(orders_router)
app.include_router(watchlist_mgmt_router)
app.include_router(simulation_router)
app.include_router(strategies_router)
app.include_router(logs_router)
app.include_router(reports_router)


@app.get("/")
def index():
    """Serve the React frontend HTML."""
    html_path = Path(__file__).resolve().parent / "frontend" / "dist" / "index.html"
    if html_path.exists():
        with open(html_path) as f:
            html = f.read()
        if "/@vite/client" in html or "Trading Dashboard" in html:
            return HTMLResponse(content=html)
    return HTMLResponse(content="Dashboard not available.")


@app.get("/api/reports/bars")
def bars_coverage_report():
    """Bar data freshness report for BarsCoverageReport.jsx."""
    from dashboard.api.reports import get_bars_coverage_fresh
    return get_bars_coverage_fresh()

@app.get("/health")
def health():
    return {"status": "ok", "service": "trading-dashboard"}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    cid = id(websocket)
    live_feed.clients.add((websocket, cid))
    if not hasattr(live_feed, '_initial_broadcast'):
        live_feed._initial_broadcast = {}
    live_feed._initial_broadcast[cid] = True
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                import json
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await live_feed.remove_client(websocket)
    except Exception as e:
        print(f"[dashboard ws] client error: {e}")
        await live_feed.remove_client(websocket)


# Serve built React frontend static files (JS/CSS)
# Mount under /static/ to avoid conflict with @app.get("/") route handler.
dist_dir = Path(__file__).resolve().parent / "frontend" / "dist"
if dist_dir.exists():
    app.mount("/static", StaticFiles(directory=str(dist_dir)), name="static")


if __name__ == "__main__":
    print(f"[dashboard] Starting on {DASHBOARD_HOST}:{DASHBOARD_PORT}")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")
