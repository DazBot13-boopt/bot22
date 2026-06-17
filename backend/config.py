import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ── Bot mode ────────────────────────────────────────────────
    mode: str = field(default_factory=lambda: os.getenv("MODE", "demo"))

    # ── Demo settings ───────────────────────────────────────────
    demo_initial_balance: float = field(
        default_factory=lambda: float(os.getenv("DEMO_INITIAL_BALANCE", "500.0"))
    )

    # ── Copy settings ───────────────────────────────────────────
    fixed_amount_per_trade: float = field(
        default_factory=lambda: float(os.getenv("FIXED_AMOUNT_PER_TRADE", "5.0"))
    )
    max_daily_spend: float = field(
        default_factory=lambda: float(os.getenv("MAX_DAILY_SPEND", "100.0"))
    )
    max_weekly_trades: int = field(
        default_factory=lambda: int(os.getenv("MAX_WEEKLY_TRADES", "10"))
    )
    max_copy_delay_seconds: int = field(
        default_factory=lambda: int(os.getenv("MAX_COPY_DELAY_SECONDS", "60"))
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
    )

    # ── Risk management ─────────────────────────────────────────
    # Sécurisation des gains : si le PnL réalisé dépasse ce seuil,
    # on réduit le montant par trade pour ne plus risquer les gains.
    profit_lock_threshold: float = field(
        default_factory=lambda: float(os.getenv("PROFIT_LOCK_THRESHOLD", "50.0"))
    )
    # Montant réduit (% du fixed_amount) quand les gains sont sécurisés
    profit_lock_ratio: float = field(
        default_factory=lambda: float(os.getenv("PROFIT_LOCK_RATIO", "0.5"))
    )

    # ── Copy signal thresholds ─────────────────────────────────
    # Minimum USDC total investi par le trader avant de copier
    # Mettre bas (ex: 2.0) pour les petits traders, haut (ex: 150) pour les baleines
    min_total_usdc: float = field(
        default_factory=lambda: float(os.getenv("MIN_TOTAL_USDC", "1.0"))
    )    # Conviction minimum (% dominant side)
    min_conviction_pct: float = field(
        default_factory=lambda: float(os.getenv("MIN_CONVICTION_PCT", "0.65"))
    )
    # % minimum du capital du trader alloué sur ce marché pour copier
    min_capital_pct: float = field(
        default_factory=lambda: float(os.getenv("MIN_CAPITAL_PCT", "0.0"))
    )
    # Bonus de signal : nb de traders experts qui ont la même position
    multi_trader_bonus: bool = field(
        default_factory=lambda: os.getenv("MULTI_TRADER_BONUS", "true").lower() == "true"
    )

    # ── Catégorie cible ─────────────────────────────────────────
    # Ne copier que les trades dans ces catégories de marché
    # Laisser vide = copier toutes catégories
    target_categories: list = field(
        default_factory=lambda: [
            c.strip() for c in os.getenv("TARGET_CATEGORIES", "Finance").split(",")
            if c.strip()
        ]
    )

    # ── Production settings ─────────────────────────────────────
    private_key: str = field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))
    wallet_address: str = field(default_factory=lambda: os.getenv("WALLET_ADDRESS", ""))
    signature_type: int = field(
        default_factory=lambda: int(os.getenv("SIGNATURE_TYPE", "1"))
    )

    # ── Dashboard authentication ────────────────────────────────
    dashboard_user: str = field(
        default_factory=lambda: os.getenv("DASHBOARD_USER", "admin")
    )
    dashboard_password: str = field(
        default_factory=lambda: os.getenv("DASHBOARD_PASSWORD", "")
    )

    # ── API endpoints ────────────────────────────────────────────
    data_api_url: str = "https://data-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137

    @property
    def is_production(self) -> bool:
        return self.mode == "production"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.dashboard_password)
