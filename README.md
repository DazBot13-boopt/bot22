# Polymarket Mirror Bot (Teya)

Ce bot permet de copier exactement les mouvements (BUY et SELL) de n'importe quel trader sur Polymarket.

## Fonctionnalités
- **Mirroring Exact** : Copie chaque achat et chaque vente du trader cible.
- **Multi-Traders** : Suivez plusieurs traders simultanément.
- **Flexible** : Ajoutez ou changez l'adresse du trader à suivre via le dashboard ou `traders.json`.
- **Mode Démo & Production** : Testez vos stratégies avec de l'argent virtuel avant de passer en réel.
- **Dashboard Web** : Interface simple pour suivre les performances et configurer le bot.

## Installation

1. Installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```

2. Configurez vos variables d'environnement dans un fichier `.env` :
   ```env
   MODE=demo
   FIXED_AMOUNT_PER_TRADE=5.0
   PRIVATE_KEY=votre_cle_privee
   WALLET_ADDRESS=votre_adresse_wallet
   ```

3. Lancez le bot :
   ```bash
   python run.py
   ```

## Configuration du Trader
Par défaut, le bot suit @anne666. Vous pouvez modifier la liste des traders dans `traders.json` ou via l'interface web.

## Structure du Projet
- `backend/monitor.py` : Surveille l'activité des traders.
- `backend/copy_engine.py` : Logique de mirroring (BUY/SELL).
- `backend/app.py` : API et Dashboard.
- `traders.json` : Liste des traders suivis.
