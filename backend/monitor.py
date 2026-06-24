"""
Monitor d'activité Polymarket
Surveille les mouvements des traders configurés via l'API Data de Polymarket.
"""

import asyncio
import logging
import time
from collections.abc import Callable
import httpx

from backend.config import Config
from backend.models import TargetTrade
from backend.traders_db import TradersDB

logger = logging.getLogger(__name__)

class TradeMonitor:
    def __init__(self, config: Config, traders_db: TradersDB):
        self.config = config
        self.traders_db = traders_db
        self._callbacks: list[Callable] = []
        self._running = False
        self._client = httpx.AsyncClient(timeout=15.0)

        self._last_seen_ts: dict[str, int] = {}
        self._seen_txs: dict[str, set[str]] = {}
        # Déduplication forte : (condition_id + side + outcome) dans une fenêtre de temps
        # wallet -> { "condid|side|outcome": timestamp_dernier_vu }
        self._seen_market_actions: dict[str, dict[str, float]] = {}
        self._DEDUP_WINDOW = 120  # secondes

    def on_new_trade(self, callback: Callable):
        self._callbacks.append(callback)

    async def fetch_recent_activity(self, wallet: str, limit: int = 20) -> list[TargetTrade]:
        url = f"{self.config.data_api_url}/activity"
        params = {
            "user": wallet,
            "limit": limit,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "type": "TRADE",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Erreur fetch activity pour {wallet}: {e}")
            return []

        profile = self.traders_db.get(wallet)
        username = profile.username if profile else wallet[:10]

        trades = []
        for item in data:
            trades.append(TargetTrade(
                timestamp=item.get("timestamp", 0),
                condition_id=item.get("conditionId", ""),
                trade_type=item.get("type", "TRADE"),
                size=float(item.get("size", 0)),
                usdc_size=float(item.get("usdcSize", 0)),
                price=float(item.get("price", 0)),
                asset=item.get("asset", ""),
                side=item.get("side", "BUY"),
                outcome_index=int(item.get("outcomeIndex", 0)),
                title=item.get("title", ""),
                slug=item.get("slug", ""),
                outcome=item.get("outcome", ""),
                tx_hash=item.get("transactionHash", ""),
                trader_wallet=wallet,
                trader_username=username,
                market_category=self._infer_category(item.get("slug", ""), item.get("title", "")),
            ))
        return trades

    @staticmethod
    def _infer_category(slug: str, title: str) -> str:
        """Déduit la catégorie d'un marché depuis son slug ou titre."""
        text = (slug + " " + title).lower()
        if any(k in text for k in ("btc", "bitcoin", "eth", "ethereum", "crypto", "xrp", "sol", "bnb", "doge", "coin")):
            return "Crypto"
        if any(k in text for k in ("election", "president", "vote", "congress", "senate", "democrat", "republican", "trump", "biden", "macron", "parti")):
            return "Politics"
        if any(k in text for k in ("nba", "nfl", "soccer", "football", "tennis", "sport", "league", "champion", "world cup", "fifa", "nhl", "mlb")):
            return "Sports"
        if any(k in text for k in ("oil", "gold", "silver", "nasdaq", "s&p", "dow", "rate", "fed", "gdp", "inflation", "crude", "wti", "stock", "market", "interest")):
            return "Finance"
        return "Other"

    async def _poll_trader(self, wallet: str):
        trades = await self.fetch_recent_activity(wallet)
        if not trades: return

        seen = self._seen_txs.setdefault(wallet, set())
        market_actions = self._seen_market_actions.setdefault(wallet, {})
        now_ts = time.time()

        # Si wallet jamais initialisé, on marque tout comme vu et on repart
        if wallet not in self._last_seen_ts:
            self._last_seen_ts[wallet] = trades[0].timestamp
            for t in trades:
                seen.add(t.tx_hash)
                key = f"{t.condition_id}|{t.side}|{t.outcome}"
                market_actions[key] = float(t.timestamp)
            logger.info(f"Wallet initialisé: {wallet[:12]}... | point de départ ts={trades[0].timestamp}")
            return

        last_ts = self._last_seen_ts[wallet]

        # Filtrer : nouveau timestamp ET tx_hash pas encore vu
        new_trades = [t for t in trades if t.timestamp > last_ts and t.tx_hash not in seen]
        new_trades.sort(key=lambda x: x.timestamp)

        for trade in new_trades:
            # Déduplication forte : même marché+side+outcome dans la fenêtre ?
            key = f"{trade.condition_id}|{trade.side}|{trade.outcome}"
            last_action_ts = market_actions.get(key, 0)
            if now_ts - last_action_ts < self._DEDUP_WINDOW:
                logger.debug(f"⏭ Dédupliqué [{trade.trader_username}] {trade.side} {trade.outcome} | {trade.title[:40]}")
                seen.add(trade.tx_hash)
                self._last_seen_ts[wallet] = max(self._last_seen_ts.get(wallet, 0), trade.timestamp)
                continue

            logger.info(f"🔔 [{trade.trader_username}] {trade.side} {trade.outcome} | {trade.title[:50]} | {trade.usdc_size:.2f} USDC")
            market_actions[key] = now_ts

            for cb in self._callbacks:
                try:
                    await cb(trade)
                except Exception as e:
                    logger.error(f"Erreur callback: {e}")

            seen.add(trade.tx_hash)
            self._last_seen_ts[wallet] = max(self._last_seen_ts.get(wallet, 0), trade.timestamp)

        # Nettoyage mémoire
        if len(seen) > 1000:
            self._seen_txs[wallet] = set(list(seen)[-500:])
        # Nettoyage des vieilles entrées market_actions
        cutoff = now_ts - self._DEDUP_WINDOW * 2
        self._seen_market_actions[wallet] = {k: v for k, v in market_actions.items() if v > cutoff}

    async def start(self):
        self._running = True
        logger.info("Démarrage du monitoring d'activité...")
        
        # Initialisation : on marque les trades actuels comme vus pour ne pas copier le passé
        wallets = self.traders_db.all_wallets()
        for w in wallets:
            initial = await self.fetch_recent_activity(w, limit=1)
            if initial:
                self._last_seen_ts[w] = initial[0].timestamp
                self._seen_txs.setdefault(w, set()).add(initial[0].tx_hash)

        while self._running:
            active_wallets = self.traders_db.all_wallets()
            for wallet in active_wallets:
                await self._poll_trader(wallet)
            await asyncio.sleep(self.config.poll_interval_seconds)

    async def check_market_resolution(self, condition_id: str):
        url = f"{self.config.clob_api_url}/markets/{condition_id}"
        try:
            resp = await self._client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("closed"):
                    for token in data.get("tokens", []):
                        if token.get("winner"):
                            return {"winner": token.get("outcome")}
        except:
            pass
        return None

    async def fetch_recent_activity_dashboard(self, limit: int = 50) -> list[TargetTrade]:
        """Agrège l'activité récente de tous les traders surveillés pour le dashboard."""
        all_trades: list[TargetTrade] = []
        wallets = self.traders_db.all_wallets()
        for wallet in wallets:
            trades = await self.fetch_recent_activity(wallet, limit=limit)
            all_trades.extend(trades)
        all_trades.sort(key=lambda t: t.timestamp, reverse=True)
        return all_trades[:limit]

    def stop(self):
        self._running = False

    async def close(self):
        await self._client.aclose()
