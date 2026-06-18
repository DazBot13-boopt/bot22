"""FastAPI application — Dashboard + API endpoints."""

import asyncio
import logging
import secrets
import time

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from backend.config import Config
from backend.copy_engine import CopyEngine
from backend.monitor import TradeMonitor
from backend.traders_db import TradersDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

config = Config()
traders_db = TradersDB()
monitor = TradeMonitor(config, traders_db)
engine = CopyEngine(config, traders_db)

app = FastAPI(title="Polymarket CopyBot", version="2.0.0")

# ── Auth ─────────────────────────────────────────────────────────────────────
_basic_auth = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_basic_auth)) -> None:
    if not config.auth_enabled:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = secrets.compare_digest(
        credentials.username.encode(), config.dashboard_user.encode()
    )
    ok_pass = secrets.compare_digest(
        credentials.password.encode(), config.dashboard_password.encode()
    )
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


AUTH = [Depends(require_auth)]


# ── Bot Controller ────────────────────────────────────────────────────────────

class BotController:
    def __init__(self) -> None:
        self.monitor_task: asyncio.Task | None = None
        self.resolver_task: asyncio.Task | None = None
        self.is_running: bool = False
        self._resolver_should_run: bool = False
        self._lock = asyncio.Lock()

    async def start(self) -> dict:
        async with self._lock:
            if self.is_running:
                return {"status": "already_running", "is_running": True, "mode": config.mode}

            engine.started_at = time.time()
            # Reset le tracker de marchés pour repartir propre
            engine._market_trackers.clear()
            monitor._running = True
            self._resolver_should_run = True
            self.is_running = True

            self.monitor_task = asyncio.create_task(monitor.start())
            self.resolver_task = asyncio.create_task(self._resolution_loop())

            active = len(traders_db.all_wallets())
            logger.info(
                "Bot démarré en mode %s | %d trader(s) surveillé(s) | catégories: %s",
                config.mode.upper(), active, config.target_categories,
            )
            return {"status": "started", "is_running": True, "mode": config.mode}

    async def stop(self) -> dict:
        async with self._lock:
            if not self.is_running:
                return {"status": "already_stopped", "is_running": False}

            self.is_running = False
            self._resolver_should_run = False
            monitor.stop()

            for task in (self.monitor_task, self.resolver_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.warning("Task terminée avec erreur: %s", e)

            self.monitor_task = None
            self.resolver_task = None
            logger.info("Bot arrêté")
            return {"status": "stopped", "is_running": False}

    async def _resolution_loop(self) -> None:
        """Vérifie périodiquement si des marchés sont résolus."""
        while self._resolver_should_run:
            try:
                unresolved_ids: set[str] = set()
                for trade in engine.trades:
                    if trade.copy_status.value == "COPIED" and not trade.resolved:
                        unresolved_ids.add(trade.target_trade.condition_id)

                for cid in unresolved_ids:
                    if not self._resolver_should_run:
                        break
                    result = await monitor.check_market_resolution(cid)
                    if result:
                        winner = result["winner"]
                        engine.resolve_position(cid, winner)
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Resolution loop: %s", e)

            for _ in range(15):
                if not self._resolver_should_run:
                    break
                await asyncio.sleep(1)


bot = BotController()


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    monitor.on_new_trade(engine.handle_trade)
    traders = traders_db.all_active()
    logger.info(
        "API prête | mode=%s | %d trader(s) | catégories=%s | auth=%s",
        config.mode.upper(),
        len(traders),
        config.target_categories,
        "ON" if config.auth_enabled else "OFF",
    )
    for t in traders:
        logger.info("  → %s (%s) | spécialité: %s | WR: %.0f%%",
                    t.username, t.wallet[:10], t.specialty, t.win_rate)


@app.on_event("shutdown")
async def shutdown() -> None:
    await bot.stop()
    await monitor.close()
    logger.info("API stoppée")


# ── Routes publiques ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "mode": config.mode, "is_running": bot.is_running})


@app.get("/", response_class=HTMLResponse)
async def dashboard(credentials: HTTPBasicCredentials | None = Depends(_basic_auth)):
    """Dashboard — protégé si DASHBOARD_PASSWORD est défini."""
    if config.auth_enabled:
        if credentials is None or not (
            secrets.compare_digest(credentials.username.encode(), config.dashboard_user.encode())
            and secrets.compare_digest(credentials.password.encode(), config.dashboard_password.encode())
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
    with open("frontend/index.html") as f:
        return HTMLResponse(content=f.read())


# ── Bot control ───────────────────────────────────────────────────────────────

@app.post("/api/start", dependencies=AUTH)
async def api_start():
    return JSONResponse(await bot.start())


@app.post("/api/stop", dependencies=AUTH)
async def api_stop():
    return JSONResponse(await bot.stop())


@app.get("/api/status", dependencies=AUTH)
async def api_status():
    return JSONResponse({
        "is_running": bot.is_running,
        "mode": config.mode,
        "started_at": engine.started_at,
        "uptime_seconds": (time.time() - engine.started_at) if engine.started_at else 0,
    })


# ── Stats & données ───────────────────────────────────────────────────────────

@app.get("/api/stats", dependencies=AUTH)
async def get_stats():
    data = engine.get_stats()
    data["is_running"] = bot.is_running
    return JSONResponse(data)


@app.get("/api/trades", dependencies=AUTH)
async def get_trades(limit: int = 50):
    return JSONResponse(engine.get_recent_trades(limit))


@app.get("/api/positions", dependencies=AUTH)
async def get_positions():
    return JSONResponse(engine.get_positions())


@app.get("/api/pnl-history", dependencies=AUTH)
async def get_pnl_history():
    return JSONResponse(engine.get_pnl_history())


@app.get("/api/target/activity", dependencies=AUTH)
async def get_target_activity():
    trades = await monitor.fetch_recent_activity_dashboard(limit=20)
    return JSONResponse([
        {
            "timestamp": t.timestamp,
            "title": t.title,
            "category": t.market_category,
            "outcome": t.outcome,
            "side": t.side,
            "price": t.price,
            "usdc_size": t.usdc_size,
            "size": t.size,
            "trader_username": t.trader_username,
        }
        for t in trades
    ])


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config", dependencies=AUTH)
async def get_config():
    return JSONResponse({
        "mode": config.mode,
        "fixed_amount_per_trade": config.fixed_amount_per_trade,
        "max_daily_spend": config.max_daily_spend,
        "max_weekly_trades": config.max_weekly_trades,
        "max_copy_delay_seconds": config.max_copy_delay_seconds,
        "poll_interval_seconds": config.poll_interval_seconds,
        "min_total_usdc": config.min_total_usdc,
        "min_conviction_pct": config.min_conviction_pct,
        "target_categories": config.target_categories,
        "profit_lock_threshold": config.profit_lock_threshold,
        "profit_lock_ratio": config.profit_lock_ratio,
        "multi_trader_bonus": config.multi_trader_bonus,
        "demo_initial_balance": config.demo_initial_balance,
        "auth_enabled": config.auth_enabled,
    })


@app.post("/api/config", dependencies=AUTH)
async def update_config(request: Request):
    data = await request.json()
    if "mode" in data:
        config.mode = data["mode"]
    if "fixed_amount_per_trade" in data:
        config.fixed_amount_per_trade = float(data["fixed_amount_per_trade"])
    if "max_daily_spend" in data:
        config.max_daily_spend = float(data["max_daily_spend"])
    if "max_weekly_trades" in data:
        config.max_weekly_trades = int(data["max_weekly_trades"])
    if "poll_interval_seconds" in data:
        config.poll_interval_seconds = int(data["poll_interval_seconds"])
    if "max_copy_delay_seconds" in data:
        config.max_copy_delay_seconds = int(data["max_copy_delay_seconds"])
    if "min_total_usdc" in data:
        config.min_total_usdc = float(data["min_total_usdc"])
    if "min_conviction_pct" in data:
        config.min_conviction_pct = float(data["min_conviction_pct"])
    if "profit_lock_threshold" in data:
        config.profit_lock_threshold = float(data["profit_lock_threshold"])
    if "profit_lock_ratio" in data:
        config.profit_lock_ratio = float(data["profit_lock_ratio"])
    if "target_categories" in data:
        config.target_categories = data["target_categories"]
    logger.info("Config mise à jour: %s", data)
    return JSONResponse({"status": "ok"})


# ── Gestion des traders (Plan 1) ──────────────────────────────────────────────

@app.get("/api/traders", dependencies=AUTH)
async def get_traders():
    return JSONResponse(traders_db.list_all())


@app.post("/api/traders", dependencies=AUTH)
async def add_trader(request: Request):
    data = await request.json()
    required = ["wallet", "username", "specialty"]
    for f in required:
        if f not in data:
            raise HTTPException(status_code=400, detail=f"Champ requis: {f}")
    profile = traders_db.add_or_update(
        wallet=data["wallet"],
        username=data["username"],
        specialty=data["specialty"],
        win_rate=float(data.get("win_rate", 0)),
        roi=float(data.get("roi", 0)),
        notes=data.get("notes", ""),
        active=bool(data.get("active", True)),
    )
    return JSONResponse({
        "status": "ok",
        "wallet": profile.wallet,
        "username": profile.username,
        "specialty": profile.specialty,
    })


@app.delete("/api/traders/{wallet}", dependencies=AUTH)
async def delete_trader(wallet: str):
    deleted = traders_db.delete(wallet)
    if not deleted:
        raise HTTPException(status_code=404, detail="Trader non trouvé")
    return JSONResponse({"status": "deleted", "wallet": wallet})


@app.post("/api/traders/{wallet}/toggle", dependencies=AUTH)
async def toggle_trader(wallet: str):
    new_state = traders_db.toggle_active(wallet)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Trader non trouvé")
    return JSONResponse({"status": "ok", "wallet": wallet, "active": new_state})
