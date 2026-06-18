from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TradeType(str, Enum):
    TRADE = "TRADE"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    REDEEM = "REDEEM"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class CopyStatus(str, Enum):
    COPIED = "COPIED"
    SOLD = "SOLD"                       # position vendue suite au SELL du trader
    SKIPPED_TOO_LATE = "SKIPPED_TOO_LATE"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"
    SKIPPED_NON_TRADE = "SKIPPED_NON_TRADE"
    SKIPPED_CATEGORY = "SKIPPED_CATEGORY"
    SKIPPED_CONVICTION = "SKIPPED_CONVICTION"
    SKIPPED_WEEKLY_LIMIT = "SKIPPED_WEEKLY_LIMIT"
    FAILED = "FAILED"


# ── Trader profile (base de données locale) ──────────────────────────────────

@dataclass
class TraderProfile:
    """Profil d'un bon trader identifié manuellement ou via betmoar."""
    wallet: str
    username: str
    specialty: str          # ex: "Finance", "Crypto", "Politics", "Sports"
    win_rate: float = 0.0   # % global betmoar
    roi: float = 0.0        # ROI global betmoar
    notes: str = ""
    active: bool = True


# ── Trade models ──────────────────────────────────────────────────────────────

@dataclass
class TargetTrade:
    """A trade detected from a target trader."""
    timestamp: int
    condition_id: str
    trade_type: str
    size: float
    usdc_size: float
    price: float
    asset: str
    side: str
    outcome_index: int
    title: str
    slug: str
    outcome: str
    tx_hash: str
    trader_wallet: str = ""     # quelle adresse a passé ce trade
    trader_username: str = ""
    market_category: str = ""   # catégorie du marché

    @property
    def datetime_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)


@dataclass
class CopiedTrade:
    """A trade that was copied (or skipped) by the bot."""
    id: int
    target_trade: TargetTrade
    copy_status: CopyStatus
    copy_timestamp: float
    amount_usdc: float = 0.0
    shares: float = 0.0
    price: float = 0.0
    pnl: float = 0.0
    resolved: bool = False
    won: bool = False
    reason: str = ""
    # Vente anticipée (quand le trader vend avant résolution)
    sold_early: bool = False
    sell_price: float = 0.0
    sell_timestamp: float = 0.0
    # Contexte décision
    conviction_pct: float = 0.0
    traders_aligned: int = 0
    trader_wallet: str = ""
    trader_username: str = ""
    trader_specialty: str = ""


@dataclass
class DemoWallet:
    """Paper trading wallet state."""
    initial_balance: float = 500.0
    balance: float = 500.0
    total_invested: float = 0.0
    total_returned: float = 0.0
    positions: dict = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return self.balance - self.initial_balance + self.total_invested - self.total_returned

    @property
    def total_pnl(self) -> float:
        positions_value = sum(
            p.get("current_value", 0) for p in self.positions.values()
        )
        return (self.balance + positions_value) - self.initial_balance


@dataclass
class BotStats:
    """Aggregated bot statistics."""
    mode: str = "demo"
    is_running: bool = False
    # Wallet
    balance: float = 0.0
    initial_balance: float = 0.0
    total_pnl: float = 0.0
    # Trades
    total_trades_detected: int = 0
    total_trades_copied: int = 0
    total_trades_skipped: int = 0
    total_trades_won: int = 0
    total_trades_lost: int = 0
    win_rate: float = 0.0
    # Weekly
    weekly_trades: int = 0
    max_weekly_trades: int = 10
    # Daily
    daily_spend: float = 0.0
    max_daily_spend: float = 100.0
    # Config
    fixed_amount_per_trade: float = 5.0
    poll_interval: int = 5
    # Uptime
    started_at: str = ""
    uptime_seconds: float = 0.0
