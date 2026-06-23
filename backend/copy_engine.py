"""
Engine de Copy Trading - Mirroring Exact
Ce module gère la logique de copie des trades détectés.
Il reproduit fidèlement les BUY et SELL sans filtres de conviction.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from backend.config import Config
from backend.models import (
    CopiedTrade,
    CopyStatus,
    DemoWallet,
    TargetTrade,
)
from backend.traders_db import TradersDB

logger = logging.getLogger(__name__)

TRADES_FILE = os.getenv("TRADES_FILE", "trades_state.json")


class CopyEngine:
    def __init__(self, config: Config, traders_db: TradersDB):
        self.config = config
        self.traders_db = traders_db
        self.trades: list[CopiedTrade] = []
        self._trade_counter = 0
        self._daily_spend = 0.0
        self._daily_reset_date: str = ""
        self._weekly_copies = 0
        self._weekly_reset_date: str = ""
        self.started_at: float = 0.0

        # Demo wallet
        self.demo_wallet = DemoWallet(
            initial_balance=config.demo_initial_balance,
            balance=config.demo_initial_balance,
        )

        # Production client (lazy init)
        self._prod_client = None
        self._tick_size_cache: dict[str, str] = {}

        # Charger l'état persisté
        self._load_state()

    # ── Persistance ────────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Sauvegarde trades + wallet démo sur disque."""
        try:
            trades_data = []
            for t in self.trades:
                tt = t.target_trade
                trades_data.append({
                    "id": t.id,
                    "copy_status": t.copy_status.value,
                    "copy_timestamp": t.copy_timestamp,
                    "amount_usdc": t.amount_usdc,
                    "shares": t.shares,
                    "price": t.price,
                    "pnl": t.pnl,
                    "resolved": t.resolved,
                    "won": t.won,
                    "reason": t.reason,
                    "sold_early": t.sold_early,
                    "sell_price": t.sell_price,
                    "sell_timestamp": t.sell_timestamp,
                    "conviction_pct": t.conviction_pct,
                    "traders_aligned": t.traders_aligned,
                    "trader_wallet": t.trader_wallet,
                    "trader_username": t.trader_username,
                    "trader_specialty": t.trader_specialty,
                    # TargetTrade fields
                    "tt_timestamp": tt.timestamp,
                    "tt_condition_id": tt.condition_id,
                    "tt_trade_type": tt.trade_type,
                    "tt_size": tt.size,
                    "tt_usdc_size": tt.usdc_size,
                    "tt_price": tt.price,
                    "tt_asset": tt.asset,
                    "tt_side": tt.side,
                    "tt_outcome_index": tt.outcome_index,
                    "tt_title": tt.title,
                    "tt_slug": tt.slug,
                    "tt_outcome": tt.outcome,
                    "tt_tx_hash": tt.tx_hash,
                    "tt_market_category": tt.market_category,
                    "tt_trader_wallet": tt.trader_wallet,
                    "tt_trader_username": tt.trader_username,
                })

            state = {
                "trade_counter": self._trade_counter,
                "daily_spend": self._daily_spend,
                "daily_reset_date": self._daily_reset_date,
                "weekly_copies": self._weekly_copies,
                "weekly_reset_date": self._weekly_reset_date,
                "demo_balance": self.demo_wallet.balance,
                "demo_total_invested": self.demo_wallet.total_invested,
                "demo_total_returned": self.demo_wallet.total_returned,
                "demo_positions": self.demo_wallet.positions,
                "trades": trades_data,
            }
            with open(TRADES_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning("Impossible de sauvegarder l'état: %s", e)

    def _load_state(self) -> None:
        """Recharge l'état depuis le fichier persisté."""
        if not os.path.exists(TRADES_FILE):
            return
        try:
            with open(TRADES_FILE) as f:
                state = json.load(f)

            self._trade_counter = state.get("trade_counter", 0)
            self._daily_spend = state.get("daily_spend", 0.0)
            self._daily_reset_date = state.get("daily_reset_date", "")
            self._weekly_copies = state.get("weekly_copies", 0)
            self._weekly_reset_date = state.get("weekly_reset_date", "")
            self.demo_wallet.balance = state.get("demo_balance", self.config.demo_initial_balance)
            self.demo_wallet.total_invested = state.get("demo_total_invested", 0.0)
            self.demo_wallet.total_returned = state.get("demo_total_returned", 0.0)
            self.demo_wallet.positions = state.get("demo_positions", {})

            for td in state.get("trades", []):
                tt = TargetTrade(
                    timestamp=td["tt_timestamp"],
                    condition_id=td["tt_condition_id"],
                    trade_type=td["tt_trade_type"],
                    size=td["tt_size"],
                    usdc_size=td["tt_usdc_size"],
                    price=td["tt_price"],
                    asset=td["tt_asset"],
                    side=td["tt_side"],
                    outcome_index=td["tt_outcome_index"],
                    title=td["tt_title"],
                    slug=td["tt_slug"],
                    outcome=td["tt_outcome"],
                    tx_hash=td["tt_tx_hash"],
                    market_category=td.get("tt_market_category", ""),
                    trader_wallet=td.get("tt_trader_wallet", ""),
                    trader_username=td.get("tt_trader_username", ""),
                )
                ct = CopiedTrade(
                    id=td["id"],
                    target_trade=tt,
                    copy_status=CopyStatus(td["copy_status"]),
                    copy_timestamp=td["copy_timestamp"],
                    amount_usdc=td["amount_usdc"],
                    shares=td["shares"],
                    price=td["price"],
                    pnl=td["pnl"],
                    resolved=td["resolved"],
                    won=td["won"],
                    reason=td["reason"],
                    sold_early=td.get("sold_early", False),
                    sell_price=td.get("sell_price", 0.0),
                    sell_timestamp=td.get("sell_timestamp", 0.0),
                    conviction_pct=td.get("conviction_pct", 0.0),
                    traders_aligned=td.get("traders_aligned", 0),
                    trader_wallet=td.get("trader_wallet", ""),
                    trader_username=td.get("trader_username", ""),
                    trader_specialty=td.get("trader_specialty", ""),
                )
                self.trades.append(ct)

            logger.info(
                "État rechargé: %d trades, balance=%.2f$, daily_spend=%.2f$",
                len(self.trades), self.demo_wallet.balance, self._daily_spend
            )
        except Exception as e:
            logger.warning("Impossible de charger l'état: %s — on repart de zéro", e)

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_spend = 0.0
            self._daily_reset_date = today
            logger.info("Budget journalier réinitialisé")

    def _reset_weekly_if_needed(self) -> None:
        now = datetime.now(tz=timezone.utc)
        week_str = f"{now.year}-W{now.isocalendar()[1]:02d}"
        if week_str != self._weekly_reset_date:
            self._weekly_copies = 0
            self._weekly_reset_date = week_str
            logger.info("Compteur hebdomadaire réinitialisé (%s)", week_str)

    def _calculate_copy_amount(self, trade: TargetTrade) -> float:
        """
        Calcule le montant à copier.
        Pour un mirroring exact, on utilise le montant fixe configuré par l'utilisateur.
        """
        return self.config.fixed_amount_per_trade

    async def handle_trade(self, trade: TargetTrade) -> CopiedTrade:
        """
        Handler principal appelé pour chaque trade détecté.
        """
        self._reset_daily_if_needed()
        self._reset_weekly_if_needed()
        now = time.time()

        # 1. Vérification du délai
        delay = now - trade.timestamp
        if delay > self.config.max_copy_delay_seconds:
            logger.warning(f"Trade ignoré (trop vieux: {delay:.0f}s): {trade.title}")
            ct = self._skip(trade, CopyStatus.SKIPPED_TOO_LATE, f"Délai: {delay:.0f}s")
            self.trades.append(ct)
            self._save_state()
            return ct

        # 2. Gestion des SELL (Vendre notre position si on en a une)
        if trade.side == "SELL":
            sold = await self._handle_sell(trade, now)
            if sold:
                return sold
            ct = self._skip(trade, CopyStatus.SKIPPED_NON_TRADE, "SELL - Pas de position ouverte à fermer")
            self.trades.append(ct)
            self._save_state()
            return ct

        # 3. Gestion des BUY (Ouvrir une position)
        if trade.side == "BUY":
            # Filtre catégorie
            if self.config.target_categories and trade.market_category not in self.config.target_categories:
                ct = self._skip(trade, CopyStatus.SKIPPED_CATEGORY,
                                f"Catégorie {trade.market_category!r} hors cible {self.config.target_categories}")
                self.trades.append(ct)
                self._save_state()
                return ct

            # Vérification des limites de budget
            amount = self._calculate_copy_amount(trade)
            if self._daily_spend + amount > self.config.max_daily_spend:
                ct = self._skip(trade, CopyStatus.SKIPPED_BUDGET, "Budget journalier dépassé")
                self.trades.append(ct)
                self._save_state()
                return ct

            if self._weekly_copies >= self.config.max_weekly_trades:
                ct = self._skip(trade, CopyStatus.SKIPPED_WEEKLY_LIMIT, "Limite hebdomadaire atteinte")
                self.trades.append(ct)
                self._save_state()
                return ct

            logger.info(f"🚀 COPIE BUY: {trade.outcome} {trade.title} @ {trade.price}")

            if self.config.is_production:
                copied = await self._execute_production(trade, amount)
            else:
                copied = self._execute_demo(trade, amount)

            copied.trader_wallet = trade.trader_wallet
            copied.trader_username = trade.trader_username

            if copied.copy_status == CopyStatus.COPIED:
                self._weekly_copies += 1
                self.trades.append(copied)
                self._save_state()
                return copied

            self.trades.append(copied)
            self._save_state()
            return copied

        return self._skip(trade, CopyStatus.SKIPPED_NON_TRADE, f"Type de trade non géré: {trade.side}")

    async def _handle_sell(self, trade: TargetTrade, now: float) -> CopiedTrade | None:
        """
        Cherche une position ouverte sur le même marché et la vend.
        """
        open_trade = None
        # On cherche la position la plus ancienne non résolue pour ce marché et ce trader
        for t in self.trades:
            if (t.target_trade.condition_id == trade.condition_id 
                and t.copy_status == CopyStatus.COPIED 
                and not t.resolved 
                and t.trader_wallet == trade.trader_wallet):
                open_trade = t
                break

        if not open_trade:
            return None

        sell_price = trade.price if trade.price > 0 else open_trade.price
        proceeds = open_trade.shares * sell_price
        pnl = proceeds - open_trade.amount_usdc

        logger.info(f"📤 VENTE COPIÉE: {trade.title} | PnL: ${pnl:.2f}")

        sell_ok = True
        if self.config.is_production:
            sell_ok = await self._execute_production_sell(open_trade, trade)

        if not sell_ok:
            return None

        # Mise à jour de la position
        open_trade.sold_early = True
        open_trade.sell_price = sell_price
        open_trade.sell_timestamp = now
        open_trade.resolved = True
        open_trade.pnl = pnl
        open_trade.won = pnl >= 0

        if not self.config.is_production:
            self.demo_wallet.balance += proceeds
            self.demo_wallet.total_returned += proceeds
            # Nettoyage positions démo
            pos_key = f"{trade.condition_id}_{open_trade.target_trade.outcome}"
            if pos_key in self.demo_wallet.positions:
                self.demo_wallet.positions[pos_key]["shares"] = 0

        self._save_state()
        return open_trade

    def _execute_demo(self, trade: TargetTrade, amount: float) -> CopiedTrade:
        price = max(trade.price, 0.01)
        shares = amount / price

        if amount > self.demo_wallet.balance:
            return self._make_copied(trade, CopyStatus.SKIPPED_BUDGET, reason="Solde démo insuffisant")

        self.demo_wallet.balance -= amount
        self.demo_wallet.total_invested += amount
        self._daily_spend += amount

        pos_key = f"{trade.condition_id}_{trade.outcome}"
        if pos_key not in self.demo_wallet.positions:
            self.demo_wallet.positions[pos_key] = {
                "title": trade.title,
                "outcome": trade.outcome,
                "shares": 0,
                "total_cost": 0,
                "avg_price": 0,
            }
        
        pos = self.demo_wallet.positions[pos_key]
        pos["shares"] += shares
        pos["total_cost"] += amount
        pos["avg_price"] = pos["total_cost"] / pos["shares"]
        pos["current_value"] = pos["shares"] * price

        return self._make_copied(trade, CopyStatus.COPIED, amount_usdc=amount, shares=shares, price=price)

    async def _execute_production(self, trade: TargetTrade, amount: float) -> CopiedTrade:
        # Implémentation réelle avec py_clob_client_v2
        try:
            client = self._ensure_prod_client()
            from py_clob_client_v2 import MarketOrderArgs, OrderType, Side, PartialCreateOrderOptions
            
            tick_size = self._get_tick_size(trade.asset)
            resp = client.create_and_post_market_order(
                order_args=MarketOrderArgs(
                    token_id=trade.asset,
                    amount=amount,
                    side=Side.BUY,
                    order_type=OrderType.FOK,
                ),
                options=PartialCreateOrderOptions(tick_size=tick_size)
            )
            
            success, reason = self._classify_order_response(resp)
            if success:
                self._daily_spend += amount
                return self._make_copied(trade, CopyStatus.COPIED, amount_usdc=amount, price=trade.price, shares=amount/trade.price)
            else:
                return self._make_copied(trade, CopyStatus.FAILED, reason=reason)
        except Exception as e:
            logger.error(f"Erreur exécution production: {e}")
            return self._make_copied(trade, CopyStatus.FAILED, reason=str(e))

    async def _execute_production_sell(self, open_trade: CopiedTrade, sell_signal: TargetTrade) -> bool:
        try:
            client = self._ensure_prod_client()
            from py_clob_client_v2 import MarketOrderArgs, OrderType, Side, PartialCreateOrderOptions
            
            tick_size = self._get_tick_size(open_trade.target_trade.asset)
            resp = client.create_and_post_market_order(
                order_args=MarketOrderArgs(
                    token_id=open_trade.target_trade.asset,
                    amount=open_trade.shares,
                    side=Side.SELL,
                    order_type=OrderType.FOK,
                ),
                options=PartialCreateOrderOptions(tick_size=tick_size)
            )
            success, _ = self._classify_order_response(resp)
            return success
        except Exception as e:
            logger.error(f"Erreur vente production: {e}")
            return False

    def _ensure_prod_client(self):
        if self._prod_client: return self._prod_client
        from py_clob_client_v2 import ClobClient
        self._prod_client = ClobClient(
            host=self.config.clob_api_url,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            funder=self.config.wallet_address
        )
        return self._prod_client

    def _get_tick_size(self, token_id: str) -> str:
        if token_id in self._tick_size_cache: return self._tick_size_cache[token_id]
        try:
            ts = self._prod_client.get_tick_size(token_id)
            self._tick_size_cache[token_id] = str(ts)
            return str(ts)
        except:
            return "0.01"

    def _classify_order_response(self, resp) -> tuple[bool, str]:
        if not resp: return False, "Pas de réponse"
        if isinstance(resp, dict) and (resp.get("orderID") or resp.get("success")):
            return True, ""
        return False, str(resp)

    def _skip(self, trade: TargetTrade, status: CopyStatus, reason: str) -> CopiedTrade:
        return self._make_copied(trade, status, reason=reason)

    def _make_copied(self, trade: TargetTrade, status: CopyStatus, amount_usdc=0, shares=0, price=0, reason="") -> CopiedTrade:
        self._trade_counter += 1
        return CopiedTrade(
            id=self._trade_counter,
            target_trade=trade,
            copy_status=status,
            copy_timestamp=time.time(),
            amount_usdc=amount_usdc,
            shares=shares,
            price=price,
            reason=reason,
            trader_wallet=trade.trader_wallet,
            trader_username=trade.trader_username
        )

    def resolve_position(self, condition_id: str, winner: str):
        for t in self.trades:
            if t.target_trade.condition_id == condition_id and t.copy_status == CopyStatus.COPIED and not t.resolved:
                t.resolved = True
                t.won = (t.target_trade.outcome == winner)
                t.pnl = (t.shares if t.won else 0) - t.amount_usdc
                if not self.config.is_production and t.won:
                    self.demo_wallet.balance += t.shares
        self._save_state()

    def get_stats(self):
        copied = [t for t in self.trades if t.copy_status == CopyStatus.COPIED]
        resolved = [t for t in copied if t.resolved]
        skipped = [t for t in self.trades if t.copy_status not in (CopyStatus.COPIED,)]

        total_won = sum(1 for t in resolved if t.won)
        total_lost = sum(1 for t in resolved if not t.won)
        win_rate = (total_won / len(resolved) * 100) if resolved else 0.0

        total_pnl = sum(t.pnl for t in resolved)

        # Unrealized value from open copied positions
        open_copied = [t for t in copied if not t.resolved]
        unrealized_value = sum(t.shares * t.price for t in open_copied)

        # Effective amount (with profit lock)
        profit_locked = total_pnl >= self.config.profit_lock_threshold and self.config.profit_lock_threshold > 0
        effective_amount = (
            self.config.fixed_amount_per_trade * self.config.profit_lock_ratio
            if profit_locked
            else self.config.fixed_amount_per_trade
        )

        # Balance
        balance = self.demo_wallet.balance if not self.config.is_production else 0.0
        initial_balance = self.config.demo_initial_balance if not self.config.is_production else 0.0

        # Uptime
        uptime_seconds = (time.time() - self.started_at) if self.started_at else 0.0
        started_at_str = (
            datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat()
            if self.started_at else None
        )

        return {
            "mode": self.config.mode,
            "balance": round(balance, 4),
            "initial_balance": round(initial_balance, 4),
            "total_pnl": round(total_pnl, 4),
            "unrealized_value": round(unrealized_value, 4),
            "effective_amount": round(effective_amount, 4),
            "fixed_amount_per_trade": self.config.fixed_amount_per_trade,
            "profit_locked": profit_locked,
            "profit_lock_threshold": self.config.profit_lock_threshold,
            "total_trades_detected": len(self.trades),
            "total_trades_copied": len(copied),
            "total_trades_skipped": len(skipped),
            "total_trades_won": total_won,
            "total_trades_lost": total_lost,
            "win_rate": round(win_rate, 2),
            "daily_spend": round(self._daily_spend, 4),
            "max_daily_spend": self.config.max_daily_spend,
            "weekly_trades": self._weekly_copies,
            "max_weekly_trades": self.config.max_weekly_trades,
            "uptime_seconds": round(uptime_seconds, 1),
            "started_at": started_at_str,
        }

    def get_recent_trades(self, limit=50):
        result = []
        for t in sorted(self.trades, key=lambda x: x.copy_timestamp, reverse=True)[:limit]:
            tt = t.target_trade
            dt = datetime.fromtimestamp(t.copy_timestamp, tz=timezone.utc).isoformat()
            result.append({
                "id": t.id,
                "datetime": dt,
                "title": tt.title,
                "side": tt.side,
                "outcome": tt.outcome,
                "category": tt.market_category,
                "copy_status": t.copy_status.value,
                "pnl": round(t.pnl, 4),
                "resolved": t.resolved,
                "won": t.won,
                "amount_usdc": round(t.amount_usdc, 4),
                "shares": round(t.shares, 4),
                "price": round(t.price, 4),
                "conviction_pct": t.conviction_pct,
                "traders_aligned": t.traders_aligned,
                "trader_username": t.trader_username or tt.trader_username,
                "trader_specialty": t.trader_specialty,
                "reason": t.reason,
            })
        return result

    def get_positions(self):
        result = []
        open_trades = [
            t for t in self.trades
            if t.copy_status == CopyStatus.COPIED and not t.resolved
        ]
        for t in open_trades:
            tt = t.target_trade
            current_value = t.shares * tt.price  # approximation prix actuel = prix d'entrée
            unrealized_pnl = current_value - t.amount_usdc
            result.append({
                "title": tt.title,
                "category": tt.market_category,
                "trader": t.trader_username or tt.trader_username,
                "outcome": tt.outcome,
                "shares": round(t.shares, 4),
                "avg_price": round(t.price, 4),
                "total_cost": round(t.amount_usdc, 4),
                "current_value": round(current_value, 4),
                "unrealized_pnl": round(unrealized_pnl, 4),
            })
        return result

    def get_pnl_history(self):
        return []
