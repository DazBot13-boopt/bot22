"""
Base de données locale des traders copiés, classés par catégorie de prédilection.

Usage :
- Ajouter un trader via l'API POST /api/traders
- Le bot vérifie automatiquement la catégorie avant de copier
- Betmoar URL : https://www.betmoar.fun/profiles pour trouver les profils
"""

import json
import logging
import os
from typing import Optional

from backend.models import TraderProfile

logger = logging.getLogger(__name__)

# Fichier persistant (survit aux redémarrages)
DB_FILE = "traders.json"

# ── Traders préconfigurés (exemples à compléter) ──────────────────────────────
# Pour chaque trader :
#   1. Trouve son adresse sur Polymarket
#   2. Va sur https://www.betmoar.fun/profiles pour voir ses stats par catégorie
#   3. Note sa catégorie de prédilection (où il gagne vraiment)
#   4. Ajoute-le ici ou via l'API dashboard

DEFAULT_TRADERS: list[dict] = [
    {
        "wallet": "0x07480f204434ad41b1705b9d1de5bbfc451092a1",
        "username": "claude7",
        "specialty": "Finance",
        "win_rate": 72.0,
        "roi": 45.0,
        "notes": "Trader Finance très actif, marchés 1 semaine",
        "active": True,
    },
    # Ajoute d'autres traders ici ou via POST /api/traders
    # {
    #     "wallet": "0x...",
    #     "username": "trader2",
    #     "specialty": "Finance",
    #     "win_rate": 65.0,
    #     "roi": 30.0,
    #     "notes": "",
    #     "active": True,
    # },
]


class TradersDB:
    """Gestion de la base de données de traders."""

    def __init__(self) -> None:
        self._traders: dict[str, TraderProfile] = {}  # wallet -> profile
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Charge depuis le fichier JSON, sinon utilise les défauts."""
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

        # Première utilisation : initialise avec les défauts
        for entry in DEFAULT_TRADERS:
            p = TraderProfile(**entry)
            self._traders[p.wallet.lower()] = p
        self._save()
        logger.info("TradersDB: %d traders par défaut chargés", len(self._traders))

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

    # ── Accès ─────────────────────────────────────────────────────────────────

    def get(self, wallet: str) -> Optional[TraderProfile]:
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
        logger.info("TradersDB: trader ajouté/mis à jour: %s (%s)", username, specialty)
        return p

    def delete(self, wallet: str) -> bool:
        key = wallet.lower()
        if key in self._traders:
            del self._traders[key]
            self._save()
            return True
        return False

    def toggle_active(self, wallet: str) -> Optional[bool]:
        key = wallet.lower()
        if key not in self._traders:
            return None
        self._traders[key].active = not self._traders[key].active
        self._save()
        return self._traders[key].active

    # ── Intelligence : multi-trader signal ───────────────────────────────────

    def count_aligned_traders(
        self,
        condition_id: str,
        dominant_side: str,
        market_trackers: dict,  # {wallet: {condition_id: tracker}}
    ) -> int:
        """
        Compte combien de traders experts (dans la même catégorie)
        ont aussi bet sur le même côté de ce marché.
        Renvoie 0 si pas d'info.
        """
        count = 0
        for wallet, tracker_map in market_trackers.items():
            if condition_id not in tracker_map:
                continue
            t = tracker_map[condition_id]
            if t.get("copied_side") == dominant_side:
                count += 1
            elif t.get("dominant_side") == dominant_side and t.get("total_usdc", 0) >= 50:
                count += 1
        return count
