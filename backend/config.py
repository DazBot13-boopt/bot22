import json
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

CONFIG_FILE = os.getenv("CONFIG_FILE", "config_override.json")


def _load_override() -> dict:
    """Charge les surcharges sauvegardées par le dashboard."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_override(data: dict) -> None:
    """Persiste les surcharges sur disque."""
    try:
        existing = _load_override()
        existing.update(data)
        with open(CONFIG_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Impossible de sauvegarder config: %s", e)


_override = _load_override()


def _get(key: str, default: str) -> str:
    """Priorité: fichier override > variable d'env > valeur par défaut."""
    if key in _override:
        return str(_override[key])
    return os.getenv(key, default)


@dataclass
class Config:
    # ── Bot mode ────────────────────────────────────────────────
    mode: str = field(default_factory=lambda: _get("MODE", "demo"))

    # ── Demo settings ───────────────────────────────────────────
    demo_initial_balance: float = field(
        default_factory=lambda: float(_get("DEMO_INITIAL_BALANCE", "500.0"))
    )

    # ── Copy settings ───────────────────────────────────────────
    fixed_amount_per_trade: float = field(
        default_factory=lambda: float(_get("FIXED_AMOUNT_PER_TRADE", "0.01"))
    )
    max_daily_spend: float = field(
        default_factory=lambda: float(_get("MAX_DAILY_SPEND", "100.0"))
    )
    max_weekly_trades: int = field(
        default_factory=lambda: int(_get("MAX_WEEKLY_TRADES", "10"))
    )
    max_copy_delay_seconds: int = field(
        default_factory=lambda: int(_get("MAX_COPY_DELAY_SECONDS", "60"))
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: int(_get("POLL_INTERVAL_SECONDS", "5"))
    )

    # ── Risk management ─────────────────────────────────────────
    profit_lock_threshold: float = field(
        default_factory=lambda: float(_get("PROFIT_LOCK_THRESHOLD", "50.0"))
    )
    profit_lock_ratio: float = field(
        default_factory=lambda: float(_get("PROFIT_LOCK_RATIO", "0.5"))
    )

    # ── Copy signal thresholds ─────────────────────────────────
    min_total_usdc: float = field(
        default_factory=lambda: float(_get("MIN_TOTAL_USDC", "1.0"))
    )
    min_conviction_pct: float = field(
        default_factory=lambda: float(_get("MIN_CONVICTION_PCT", "0.65"))
    )
    min_capital_pct: float = field(
        default_factory=lambda: float(_get("MIN_CAPITAL_PCT", "0.0"))
    )
    multi_trader_bonus: bool = field(
        default_factory=lambda: _get("MULTI_TRADER_BONUS", "true").lower() == "true"
    )

    # ── Catégorie cible ─────────────────────────────────────────
    target_categories: list = field(
        default_factory=lambda: [
            c.strip() for c in _get("TARGET_CATEGORIES", "Finance").split(",")
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

    def save(self, data: dict) -> None:
        """Applique et persiste une mise à jour de config depuis le dashboard."""
        mapping = {
            "mode": ("mode", str),
            "fixed_amount_per_trade": ("fixed_amount_per_trade", float),
            "max_daily_spend": ("max_daily_spend", float),
            "max_weekly_trades": ("max_weekly_trades", int),
            "poll_interval_seconds": ("poll_interval_seconds", int),
            "max_copy_delay_seconds": ("max_copy_delay_seconds", int),
            "min_total_usdc": ("min_total_usdc", float),
            "min_conviction_pct": ("min_conviction_pct", float),
            "profit_lock_threshold": ("profit_lock_threshold", float),
            "profit_lock_ratio": ("profit_lock_ratio", float),
            "target_categories": ("target_categories", list),
        }
        to_save = {}
        for key, (attr, cast) in mapping.items():
            if key in data:
                val = data[key] if cast is list else cast(data[key])
                setattr(self, attr, val)
                # Stocker avec clé ENV pour cohérence
                to_save[key] = val
        _save_override(to_save)

    @property
    def is_production(self) -> bool:
        return self.mode == "production"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.dashboard_password)
