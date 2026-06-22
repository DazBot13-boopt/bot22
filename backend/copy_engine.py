"""
Copy trading engine — Plans 1, 2, 3 complets :

Plan 1 : Multi-traders + catégorie de prédilection
Plan 2 : Filtre catégorie, conviction %, signal multi-traders
Plan 3 : Budget journalier, limite hebdo, sécurisation des gains
"""

import logging
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

        # Trackers par trader par marché
        # {wallet: {condition_id: tracker_dict}}
        self._market_trackers: dict[str, dict[str, dict]] = {}

        # Demo wallet
        self.demo_wallet = DemoWallet(
            initial_balance=config.demo_initial_balance,
            balance=config.demo_initial_balance,
        )

        # Production client (lazy init)
        self._prod_client = None
        self._tick_size_cache: dict[str, str] = {}

    # ── Réinitialisations périodiques ────────────────────────────────────────

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_spend = 0.0
            self._daily_reset_date = today
            logger.info("Budget journalier réinitialisé")

    def _reset_weekly_if_needed(self) -> None:
        # Semaine ISO : ex "2024-W22"
        now = datetime.now(tz=timezone.utc)
        week_str = f"{now.year}-W{now.isocalendar()[1]:02d}"
        if week_str != self._weekly_reset_date:
            self._weekly_copies = 0
            self._weekly_reset_date = week_str
            logger.info("Compteur hebdomadaire réinitialisé (%s)", week_str)

    # ── Calcul montant à copier (avec sécurisation des gains) ─────────────────

    def _calculate_copy_amount(self) -> float:
        """
        Montant de base, réduit si les gains dépassent le seuil de sécurisation.
        Ex: si profit_lock_threshold=50$ de gains réalisés → on réduit le montant
        pour ne plus risquer ces gains acquis.
        """
        base = self.config.fixed_amount_per_trade

        # Calcul PnL réalisé actuel
        realized_pnl = sum(t.pnl for t in self.trades if t.resolved)

        if realized_pnl >= self.config.profit_lock_threshold:
            reduced = base * self.config.profit_lock_ratio
            logger.info(
                "Sécurisation gains: PnL=+$%.2f >= seuil $%.2f → montant réduit $%.2f",
                realized_pnl, self.config.profit_lock_threshold, reduced,
            )
            return reduced

        return base

    # ── Handler principal (appelé pour chaque trade détecté) ──────────────────

    async def handle_trade(self, trade: TargetTrade) -> CopiedTrade:
        """
        Mirror trading pur — copie chaque BUY et chaque SELL du trader,
        avec le montant fixe configuré. Aucun filtre catégorie/conviction.
        Seules limites : délai max, budget journalier, limite hebdo.
        """
        self._reset_daily_if_needed()
        self._reset_weekly_if_needed()
        now = time.time()

        # ── Filtre délai ──────────────────────────────────────────────────────
        delay = now - trade.timestamp
        if delay > self.config.max_copy_delay_seconds:
            ct = self._skip(trade, CopyStatus.SKIPPED_TOO_LATE, f"Délai: {delay:.0f}s")
            self.trades.append(ct)
            return ct

        # ── Tracker marché (pour la vente) ────────────────────────────────────
        wallet = trade.trader_wallet
        if wallet not in self._market_trackers:
            self._market_trackers[wallet] = {}
        cid = trade.condition_id
        tracker_map = self._market_trackers[wallet]
        if cid not in tracker_map:
            tracker_map[cid] = {
                "copied_side": None,
                "title": trade.title,
                "category": trade.market_category,
                "asset": trade.asset,
                "slug": trade.slug,
            }
        tracker = tracker_map[cid]

        # ── SELL → vendre notre position ──────────────────────────────────────
        if trade.side == "SELL":
            sold = await self._handle_sell(trade, cid, tracker, now)
            if sold:
                return sold
            ct = self._skip(trade, CopyStatus.SKIPPED_NON_TRADE, "SELL — pas de position ouverte")
            self.trades.append(ct)
            return ct

        # ── BUY → copier immédiatement ────────────────────────────────────────

        # Budget journalier
        amount = self._calculate_copy_amount()
        if self._daily_spend + amount > self.config.max_daily_spend:
            ct = self._make_copied(trade, CopyStatus.SKIPPED_BUDGET, reason="Budget journalier dépassé")
            self.trades.append(ct)
            return ct

        # Limite hebdomadaire
        if self._weekly_copies >= self.config.max_weekly_trades:
            ct = self._make_copied(trade, CopyStatus.SKIPPED_WEEKLY_LIMIT,
                                   reason=f"Limite hebdo: {self._weekly_copies}/{self.config.max_weekly_trades}")
            self.trades.append(ct)
            return ct

        logger.info(
            "✅ MIRROR BUY: %s %s @ %.4f | Trader=%s | $%.2f",
            trade.outcome, trade.title[:45], trade.price,
            trade.trader_username, amount,
        )

        if self.config.is_production:
            copied = await self._execute_production(trade, amount)
        else:
            copied = self._execute_demo(trade, amount)

        profile = self.traders_db.get(wallet)
        copied.trader_wallet = wallet
        copied.trader_username = trade.trader_username
        copied.trader_specialty = profile.specialty if profile else ""

        if copied.copy_status == CopyStatus.COPIED:
            tracker["copied_side"] = trade.outcome
            self._weekly_copies += 1

        self.trades.append(copied)
        return copied

    # ── Gestion des SELL ──────────────────────────────────────────────────────

    async def _handle_sell(
        self, trade: TargetTrade, cid: str, tracker: dict, now: float
    ) -> "CopiedTrade | None":
        """
        Quand le trader vend, on cherche si on a une position ouverte
        sur ce même marché et on la vend aussi.
        """
        import time as _time

        # Chercher notre position copiée sur ce marché
        open_trade = None
        for t in self.trades:
            if (
                t.target_trade.condition_id == cid
                and t.copy_status == CopyStatus.COPIED
                and not t.resolved
                and not t.sold_early
                and t.trader_wallet == trade.trader_wallet
            ):
                open_trade = t
                break

        if open_trade is None:
            return None

        # Prix de vente = prix actuel du trade du trader
        sell_price = trade.price if trade.price > 0 else open_trade.price
        proceeds = open_trade.shares * sell_price
        pnl = proceeds - open_trade.amount_usdc

        result = "+" if pnl >= 0 else ""
        logger.info(
            "📤 VENTE COPIÉE: %s | %.4f shares @ %.4f → $%.2f | PnL: %s$%.2f",
            trade.title[:50], open_trade.shares, sell_price, proceeds, result, abs(pnl)
        )

        # En production : vendre AVANT de marquer comme résolu
        sell_ok = True
        if self.config.is_production:
            sell_ok = await self._execute_production_sell(open_trade, trade)

        if not sell_ok:
            logger.error("Vente production échouée — position conservée ouverte")
            return None

        # Mettre à jour la position (seulement si vente OK)
        open_trade.sold_early = True
        open_trade.sell_price = sell_price
        open_trade.sell_timestamp = now
        open_trade.resolved = True
        open_trade.pnl = pnl
        open_trade.won = pnl >= 0

        # Mettre à jour le wallet demo
        if not self.config.is_production:
            self.demo_wallet.balance += proceeds
            self.demo_wallet.total_returned += proceeds
            pos_key = f"{cid}_{open_trade.target_trade.outcome}"
            if pos_key in self.demo_wallet.positions:
                self.demo_wallet.positions[pos_key]["shares"] = 0
                self.demo_wallet.positions[pos_key]["current_value"] = 0

        return open_trade

    async def _execute_production_sell(self, open_trade: "CopiedTrade", sell_signal: TargetTrade) -> bool:
        """Vente réelle sur Polymarket. Retourne True si succès."""
        try:
            self._ensure_prod_client()
            from py_clob_client_v2 import MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side

            tick_size = self._get_tick_size(open_trade.target_trade.asset)
            resp = self._prod_client.create_and_post_market_order(
                order_args=MarketOrderArgs(
                    token_id=open_trade.target_trade.asset,
                    amount=open_trade.shares,
                    side=Side.SELL,
                    order_type=OrderType.FOK,
                ),
                options=PartialCreateOrderOptions(tick_size=tick_size),
                order_type=OrderType.FOK,
            )
            success, reason = self._classify_order_response(resp)
            if success:
                logger.info("PROD SELL OK: %s | resp: %s", open_trade.target_trade.title[:40], resp)
            else:
                logger.error("PROD SELL REJETÉ: %s | %s", open_trade.target_trade.title[:40], reason)
            return success
        except Exception as e:
            logger.error("Prod sell échoué: %s", e)
            return False

    # ── Exécution Demo ────────────────────────────────────────────────────────

    def _execute_demo(self, trade: TargetTrade, amount: float) -> CopiedTrade:
        price = max(trade.price, 0.01)
        shares = amount / price

        if amount > self.demo_wallet.balance:
            return self._make_copied(
                trade, CopyStatus.SKIPPED_BUDGET,
                reason=f"Solde demo insuffisant: ${self.demo_wallet.balance:.2f}"
            )

        self.demo_wallet.balance -= amount
        self.demo_wallet.total_invested += amount
        self._daily_spend += amount

        position_key = f"{trade.condition_id}_{trade.outcome}"
        if position_key not in self.demo_wallet.positions:
            self.demo_wallet.positions[position_key] = {
                "condition_id": trade.condition_id,
                "outcome": trade.outcome,
                "title": trade.title,
                "slug": trade.slug,
                "category": trade.market_category,
                "shares": 0,
                "avg_price": 0,
                "total_cost": 0,
                "current_value": 0,
                "side": trade.side,
                "asset": trade.asset,
                "trader": trade.trader_username,
            }

        pos = self.demo_wallet.positions[position_key]
        pos["shares"] += shares
        pos["total_cost"] += amount
        if pos["shares"] > 0:
            pos["avg_price"] = pos["total_cost"] / pos["shares"]
        pos["current_value"] = pos["shares"] * price

        logger.info(
            "DEMO: %s %s %.2f shares @ %.4f ($%.2f) | %s",
            trade.side, trade.outcome, shares, price, amount, trade.title[:50],
        )

        return self._make_copied(
            trade, CopyStatus.COPIED,
            amount_usdc=amount, shares=shares, price=price,
        )

    # ── Exécution Production ──────────────────────────────────────────────────

    def _ensure_prod_client(self):
        if self._prod_client is not None:
            return self._prod_client
        if not self.config.private_key:
            raise RuntimeError("PRIVATE_KEY vide — impossible de placer des ordres réels")
        if not self.config.wallet_address:
            raise RuntimeError("WALLET_ADDRESS vide — impossible de placer des ordres réels")

        from py_clob_client_v2 import ClobClient
        temp_client = ClobClient(
            host=self.config.clob_api_url,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
        )
        creds = temp_client.create_or_derive_api_key()
        self._prod_client = ClobClient(
            host=self.config.clob_api_url,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            creds=creds,
            signature_type=self.config.signature_type,
            funder=self.config.wallet_address,
        )
        logger.info(
            "CLOB client init: signature_type=%d, funder=%s",
            self.config.signature_type, self.config.wallet_address,
        )
        return self._prod_client

    def _get_tick_size(self, token_id: str) -> str:
        if token_id in self._tick_size_cache:
            return self._tick_size_cache[token_id]
        try:
            ts = self._prod_client.get_tick_size(token_id)
            ts_str = str(ts) if not isinstance(ts, str) else ts
        except Exception as e:
            logger.warning("tick_size pour %s: %s → fallback 0.01", token_id[:16], e)
            ts_str = "0.01"
        self._tick_size_cache[token_id] = ts_str
        return ts_str

    @staticmethod
    def _classify_order_response(resp) -> tuple[bool, str]:
        if resp is None:
            return False, "Réponse vide du CLOB"
        if not isinstance(resp, dict):
            return True, ""
        if resp.get("errorMsg"):
            return False, str(resp["errorMsg"])
        if resp.get("success") is False:
            return False, f"success=False: {resp}"
        status = str(resp.get("status", "")).lower()
        if status in {"failed", "rejected", "cancelled", "canceled"}:
            return False, f"status={status}"
        if resp.get("orderID") or resp.get("orderId") or status in {"matched", "live", "filled"}:
            return True, ""
        return False, f"Réponse CLOB ambiguë: {resp}"

    async def _execute_production(self, trade: TargetTrade, amount: float) -> CopiedTrade:
        try:
            self._ensure_prod_client()
            from py_clob_client_v2 import (
                MarketOrderArgs,
                OrderType,
                PartialCreateOrderOptions,
                Side,
            )

            side = Side.BUY if trade.side == "BUY" else Side.SELL
            tick_size = self._get_tick_size(trade.asset)

            resp = self._prod_client.create_and_post_market_order(
                order_args=MarketOrderArgs(
                    token_id=trade.asset,
                    amount=amount,
                    side=side,
                    order_type=OrderType.FOK,
                ),
                options=PartialCreateOrderOptions(tick_size=tick_size),
                order_type=OrderType.FOK,
            )

            success, reason = self._classify_order_response(resp)
            if not success:
                logger.error(
                    "PROD REJETÉ: %s | tick=%s | raison=%s | resp=%s",
                    trade.title[:50], tick_size, reason, resp,
                )
                return self._make_copied(trade, CopyStatus.FAILED, reason=reason)

            logger.info("PROD OK: %s | $%.2f | %s", trade.title[:50], amount, resp)
            self._daily_spend += amount

            return self._make_copied(
                trade, CopyStatus.COPIED,
                amount_usdc=amount,
                shares=amount / trade.price if trade.price > 0 else 0,
                price=trade.price,
            )
        except Exception as e:
            logger.error("Ordre production échoué: %s", e)
            return self._make_copied(trade, CopyStatus.FAILED, reason=str(e))

    # ── Résolution des marchés ────────────────────────────────────────────────

    def resolve_position(self, condition_id: str, winning_outcome: str) -> None:
        """Résout une position quand le marché se clôture."""
        resolved_count = 0
        for trade in self.trades:
            if (
                trade.target_trade.condition_id == condition_id
                and trade.copy_status == CopyStatus.COPIED
                and not trade.resolved
            ):
                trade.resolved = True
                trade.won = trade.target_trade.outcome == winning_outcome
                if trade.won:
                    winnings = trade.shares * 1.0  # $1 par share si gagné
                    trade.pnl = winnings - trade.amount_usdc
                    if not self.config.is_production:
                        self.demo_wallet.balance += winnings
                        self.demo_wallet.total_returned += winnings
                    logger.info(
                        "🏆 GAGNÉ: %s | +$%.2f",
                        trade.target_trade.title[:50], trade.pnl
                    )
                else:
                    trade.pnl = -trade.amount_usdc
                    logger.info(
                        "❌ PERDU: %s | -$%.2f",
                        trade.target_trade.title[:50], abs(trade.pnl)
                    )
                resolved_count += 1

        if resolved_count:
            logger.info("Marché résolu (%s): %d position(s) clôturée(s)", condition_id[:16], resolved_count)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _skip(self, trade: TargetTrade, status: CopyStatus, reason: str) -> CopiedTrade:
        return self._make_copied(trade, status, reason=reason)

    def _make_copied(
        self,
        trade: TargetTrade,
        status: CopyStatus,
        amount_usdc: float = 0,
        shares: float = 0,
        price: float = 0,
        reason: str = "",
    ) -> CopiedTrade:
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
            trader_username=trade.trader_username,
        )

    # ── Stats & API data ──────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        self._reset_daily_if_needed()
        self._reset_weekly_if_needed()

        copied = [t for t in self.trades if t.copy_status == CopyStatus.COPIED]
        resolved = [t for t in copied if t.resolved]
        won = [t for t in resolved if t.won]
        skipped = [t for t in self.trades if t.copy_status != CopyStatus.COPIED]
        total_pnl = sum(t.pnl for t in resolved)
        unrealized_value = sum(
            p.get("current_value", 0) for p in self.demo_wallet.positions.values()
        )
        realized_pnl = sum(t.pnl for t in resolved)

        return {
            "mode": self.config.mode,
            "is_running": True,
            "balance": self.demo_wallet.balance if not self.config.is_production else 0,
            "initial_balance": self.demo_wallet.initial_balance if not self.config.is_production else 0,
            "total_pnl": total_pnl,
            "unrealized_value": unrealized_value,
            "realized_pnl": realized_pnl,
            "profit_locked": realized_pnl >= self.config.profit_lock_threshold,
            "profit_lock_threshold": self.config.profit_lock_threshold,
            "total_trades_detected": len(self.trades),
            "total_trades_copied": len(copied),
            "total_trades_skipped": len(skipped),
            "total_trades_won": len(won),
            "total_trades_lost": len(resolved) - len(won),
            "total_resolved": len(resolved),
            "win_rate": (len(won) / len(resolved) * 100) if resolved else 0,
            "daily_spend": self._daily_spend,
            "max_daily_spend": self.config.max_daily_spend,
            "weekly_trades": self._weekly_copies,
            "max_weekly_trades": self.config.max_weekly_trades,
            "fixed_amount_per_trade": self.config.fixed_amount_per_trade,
            "effective_amount": self._calculate_copy_amount(),
            "poll_interval": self.config.poll_interval_seconds,
            "target_categories": self.config.target_categories,
            "started_at": (
                datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat()
                if self.started_at else ""
            ),
            "uptime_seconds": time.time() - self.started_at if self.started_at else 0,
        }

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        recent = sorted(self.trades, key=lambda t: t.copy_timestamp, reverse=True)[:limit]
        result = []
        for t in recent:
            result.append({
                "id": t.id,
                "timestamp": t.copy_timestamp,
                "datetime": datetime.fromtimestamp(
                    t.copy_timestamp, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "title": t.target_trade.title,
                "category": t.target_trade.market_category,
                "outcome": t.target_trade.outcome,
                "side": t.target_trade.side,
                "target_price": t.target_trade.price,
                "target_size_usdc": t.target_trade.usdc_size,
                "copy_status": t.copy_status.value,
                "amount_usdc": t.amount_usdc,
                "shares": t.shares,
                "price": t.price,
                "pnl": t.pnl,
                "resolved": t.resolved,
                "won": t.won,
                "reason": t.reason,
                "conviction_pct": t.conviction_pct,
                "traders_aligned": t.traders_aligned,
                "trader_username": t.trader_username,
                "trader_specialty": t.trader_specialty,
                "sold_early": t.sold_early,
                "sell_price": t.sell_price,
            })
        return result

    def get_positions(self) -> list[dict]:
        if self.config.is_production:
            return []
        positions = []
        for key, pos in self.demo_wallet.positions.items():
            if pos["shares"] > 0:
                positions.append({
                    "key": key,
                    "title": pos["title"],
                    "category": pos.get("category", ""),
                    "outcome": pos["outcome"],
                    "trader": pos.get("trader", ""),
                    "shares": pos["shares"],
                    "avg_price": pos["avg_price"],
                    "total_cost": pos["total_cost"],
                    "current_value": pos["current_value"],
                    "unrealized_pnl": pos["current_value"] - pos["total_cost"],
                })
        return positions

    def get_pnl_history(self) -> list[dict]:
        sorted_trades = sorted(self.trades, key=lambda t: t.copy_timestamp)
        history = []
        cumulative = 0.0
        for t in sorted_trades:
            if t.resolved:
                cumulative += t.pnl
            history.append({
                "timestamp": t.copy_timestamp,
                "cumulative_pnl": cumulative,
                "trade_pnl": t.pnl if t.resolved else 0,
                "status": t.copy_status.value,
            })
        return history
