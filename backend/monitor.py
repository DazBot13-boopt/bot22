"""
Monitore l'activité de PLUSIEURS traders cibles sur Polymarket.
Chaque trader est surveillé indépendamment via polling.
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

# Catégories connues de Polymarket (mots-clés dans le titre du marché)
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Finance": [
        "bitcoin", "btc", "eth", "ethereum", "crypto", "fed", "interest rate",
        "inflation", "gdp", "s&p", "nasdaq", "dow", "stock", "dollar", "eur",
        "gold", "silver", "oil", "crude", "commodit", "treasury", "yield", "bond",
        "market", "economy", "recession", "cpi", "fomc", "rate cut", "rate hike",
        "price", "usd", "gbp", "jpy", "yen", "hit", "reach", "dip", "high", "low",
        "anthropic", "openai", "valuation", "market cap", "ipo",
        "solana", "sol", "xrp", "ripple", "bnb", "altcoin",
    ],
    "Politics": [
        "election", "president", "senate", "house", "congress", "vote",
        "trump", "biden", "harris", "democrat", "republican", "poll",
        "approval", "party", "candidate", "primary", "debate",
    ],
    "Sports": [
        "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
        "baseball", "tennis", "golf", "ufc", "mma", "champion", "super bowl",
        "world cup", "playoff", "game", "match", "tournament",
    ],
    "Crypto": [
        "bitcoin", "btc", "eth", "ethereum", "solana", "sol", "xrp", "ripple",
        "bnb", "usdc", "defi", "nft", "blockchain", "altcoin", "memecoin",
        "doge", "shib", "polygon", "matic", "chainlink",
    ],
    "World": [
        "war", "ukraine", "russia", "china", "taiwan", "middle east",
        "nato", "un ", "united nations", "ceasefire", "sanction",
        "nuclear", "conflict", "treaty", "summit",
    ],
}


def detect_category(title: str) -> str:
    """Détecte la catégorie d'un marché à partir de son titre."""
    title_lower = title.lower()
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in title_lower)
        if score > 0:
            scores[category] = score
    if not scores:
        return "Other"
    return max(scores, key=lambda k: scores[k])


class TradeMonitor:
    def __init__(self, config: Config, traders_db: TradersDB):
        self.config = config
        self.traders_db = traders_db
        self._callbacks: list[Callable] = []
        self._running = False
        self._client = httpx.AsyncClient(timeout=15.0)

        # Par trader : dernier timestamp et hashes vus
        self._last_seen: dict[str, int] = {}   # wallet -> timestamp
        self._seen_hashes: dict[str, set[str]] = {}  # wallet -> set of tx_hash

    def on_new_trade(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    # ── Fetch activity pour UN trader ────────────────────────────────────────

    async def fetch_recent_activity(
        self, wallet: str, limit: int = 50
    ) -> list[TargetTrade]:
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
            logger.error("Fetch activity [%s]: %s", wallet[:10], e)
            return []

        profile = self.traders_db.get(wallet)
        username = profile.username if profile else wallet[:10]

        trades = []
        for item in data:
            title = item.get("title", "")
            category = detect_category(title)
            trade = TargetTrade(
                timestamp=item.get("timestamp", 0),
                condition_id=item.get("conditionId", ""),
                trade_type=item.get("type", "TRADE"),
                size=float(item.get("size", 0)),
                usdc_size=float(item.get("usdcSize", 0)),
                price=float(item.get("price", 0)),
                asset=item.get("asset", ""),
                side=item.get("side", "BUY"),
                outcome_index=int(item.get("outcomeIndex", 0)),
                title=title,
                slug=item.get("slug", ""),
                outcome=item.get("outcome", ""),
                tx_hash=item.get("transactionHash", ""),
                trader_wallet=wallet,
                trader_username=username,
                market_category=category,
            )
            trades.append(trade)
        return trades

    # ── Activité récente du 1er trader (pour le dashboard) ───────────────────

    async def fetch_recent_activity_dashboard(self, limit: int = 20) -> list[TargetTrade]:
        """Retourne l'activité récente du premier trader actif (pour le dashboard)."""
        wallets = self.traders_db.all_wallets()
        if not wallets:
            return []
        return await self.fetch_recent_activity(wallets[0], limit)

    # ── Polling d'UN trader ───────────────────────────────────────────────────

    async def _poll_trader(self, wallet: str) -> None:
        trades = await self.fetch_recent_activity(wallet, limit=50)
        last_ts = self._last_seen.get(wallet, 0)
        seen = self._seen_hashes.setdefault(wallet, set())

        new_trades = []
        for trade in trades:
            if trade.tx_hash in seen:
                continue
            if trade.timestamp <= last_ts:
                continue
            new_trades.append(trade)
            seen.add(trade.tx_hash)

        if new_trades:
            new_trades.sort(key=lambda t: t.timestamp)
            for trade in new_trades:
                self._last_seen[wallet] = max(
                    self._last_seen.get(wallet, 0), trade.timestamp
                )
                profile = self.traders_db.get(wallet)
                logger.info(
                    "[%s/%s] Nouveau trade: %s %s @ %.4f ($%.2f) [%s]",
                    trade.trader_username,
                    trade.market_category,
                    trade.side,
                    trade.outcome,
                    trade.price,
                    trade.usdc_size,
                    trade.title[:50],
                )
                for cb in self._callbacks:
                    try:
                        await cb(trade)
                    except Exception as e:
                        logger.error("Callback error: %s", e)

        # Keep seen set manageable
        if len(seen) > 5000:
            self._seen_hashes[wallet] = set(list(seen)[-2000:])

    # ── Démarrage du monitoring ───────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        # Reset complet à chaque démarrage
        self._last_seen.clear()
        self._seen_hashes.clear()
        wallets = self.traders_db.all_wallets()
        logger.info("Monitoring %d traders, poll toutes les %ds", len(wallets), self.config.poll_interval_seconds)

        # Seed initial : marque comme vus uniquement les trades de plus de SEED_WINDOW secondes.
        # Les trades récents (dans la fenêtre) seront rejoués et évalués par le copy engine.
        # Seed initial : marque comme vus les vieux trades (>10min).
        # Les trades récents (<10min) ne sont PAS mis dans seen → seront détectés au premier poll.
        SEED_WINDOW = 600  # secondes
        now_ts = int(time.time())
        for wallet in wallets:
            initial = await self.fetch_recent_activity(wallet, limit=100)
            seen = self._seen_hashes.setdefault(wallet, set())
            replayed = 0
            for t in initial:
                if t.timestamp < now_ts - SEED_WINDOW:
                    # Trade vieux : marquer vu ET mettre à jour last_seen
                    seen.add(t.tx_hash)
                    self._last_seen[wallet] = max(self._last_seen.get(wallet, 0), t.timestamp)
                else:
                    # Trade récent : NE PAS mettre dans seen ni last_seen
                    # → il sera capté au prochain poll
                    replayed += 1

            # last_seen doit être juste AVANT le premier trade récent
            # pour que le poll le détecte correctement
            if replayed > 0 and wallet in self._last_seen:
                # Trouver le timestamp du plus vieux trade récent - 1
                recent = [t for t in initial if t.timestamp >= now_ts - SEED_WINDOW]
                if recent:
                    oldest_recent = min(t.timestamp for t in recent)
                    self._last_seen[wallet] = min(self._last_seen.get(wallet, 0), oldest_recent - 1)

            logger.info(
                "Seed [%s]: %d trades anciens ignorés, %d trades récents (<10min) à rejouer",
                wallet[:10], len(initial) - replayed, replayed
            )

        # Boucle principale
        while self._running:
            # Recharge la liste (au cas où un trader a été ajouté dynamiquement)
            active_wallets = self.traders_db.all_wallets()
            tasks = [self._poll_trader(w) for w in active_wallets]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error("Poll error [%s]: %s", active_wallets[i][:10], r)

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def check_market_resolution(self, condition_id: str) -> dict | None:
        """Vérifie si un marché est résolu via l'API CLOB."""
        url = f"{self.config.clob_api_url}/markets/{condition_id}"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug("Check résolution %s: %s", condition_id[:16], e)
            return None

        if not data.get("closed"):
            return None

        winning_outcome = None
        for token in data.get("tokens", []):
            if token.get("winner"):
                winning_outcome = token.get("outcome")
                break

        if winning_outcome:
            return {"condition_id": condition_id, "winner": winning_outcome}
        return None

    def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        self.stop()
        await self._client.aclose()
