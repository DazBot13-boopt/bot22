import json
import logging
import os
from backend.models import TraderProfile

logger = logging.getLogger(__name__)

# Fichier persistant
DB_FILE = "traders.json"

class TradersDB:
    """Gestion de la base de données de traders."""

    def __init__(self) -> None:
        self._traders: dict[str, TraderProfile] = {}  # wallet -> profile
        self._load()

    def _load(self) -> None:
        """Charge depuis le fichier JSON."""
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE) as f:
                    data = json.load(f)
                for entry in data:
                    p = TraderProfile(**entry)
                    self._traders[p.wallet.lower()] = p
                logger.info("TradersDB: %d traders chargés depuis %s", len(self._traders), DB_FILE)
                return
            except Exception as e:
                logger.warning("TradersDB: impossible de lire %s: %s", DB_FILE, e)

    def _save(self) -> None:
        try:
            data = [
                {
                    "wallet": p.wallet,
                    "username": p.username,
                    "specialty": p.specialty,
                    "win_rate": p.win_rate,
                    "roi": p.roi,
                    "notes": p.notes,
                    "active": p.active,
                }
                for p in self._traders.values()
            ]
            with open(DB_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("TradersDB: sauvegarde échouée: %s", e)

    def get(self, wallet: str) -> TraderProfile | None:
        return self._traders.get(wallet.lower())

    def all_active(self) -> list[TraderProfile]:
        return [p for p in self._traders.values() if p.active]

    def all_wallets(self) -> list[str]:
        """Retourne les adresses de tous les traders actifs."""
        return [p.wallet for p in self._traders.values() if p.active]

    def list_all(self) -> list[dict]:
        return [
            {
                "wallet": p.wallet,
                "username": p.username,
                "specialty": p.specialty,
                "win_rate": p.win_rate,
                "roi": p.roi,
                "notes": p.notes,
                "active": p.active,
            }
            for p in self._traders.values()
        ]

    def add_or_update(self, wallet: str, username: str, specialty: str,
                      win_rate: float = 0.0, roi: float = 0.0,
                      notes: str = "", active: bool = True) -> TraderProfile:
        p = TraderProfile(
            wallet=wallet,
            username=username,
            specialty=specialty,
            win_rate=win_rate,
            roi=roi,
            notes=notes,
            active=active,
        )
        self._traders[wallet.lower()] = p
        self._save()
        logger.info("TradersDB: trader ajouté/mis à jour: %s", username)
        return p

    def delete(self, wallet: str) -> bool:
        key = wallet.lower()
        if key in self._traders:
            del self._traders[key]
            self._save()
            return True
        return False

    def toggle_active(self, wallet: str) -> bool | None:
        key = wallet.lower()
        if key not in self._traders:
            return None
        self._traders[key].active = not self._traders[key].active
        self._save()
        return self._traders[key].active
