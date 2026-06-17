# Polymarket CopyBot

Bot de copy-trading qui suit automatiquement les trades de **@claude7** sur Polymarket et les replique.

## Fonctionnalites

- **Mode Demo** : Paper trading avec un wallet virtuel (pas d'argent reel)
- **Mode Production** : Trades reels via l'API Polymarket CLOB
- **Dashboard web** : Stats en temps reel, P&L, historique des trades, positions ouvertes
- **Bouton Start / Stop** : Demarrer et arreter le bot directement depuis l'interface
- **Configuration live** : Modifier les parametres sans redemarrer le bot
- **Auto-refresh** : Le dashboard se met a jour toutes les 3 secondes
- **Pret pour DigitalOcean** : Dockerfile + `.do/app.yaml` inclus (voir [`DEPLOY_DIGITAL_OCEAN.md`](DEPLOY_DIGITAL_OCEAN.md))
- **Securite** : auth HTTP Basic sur le dashboard, validation des ordres CLOB, tick_size dynamique, `signature_type` configurable

## Installation

```bash
pip install fastapi uvicorn[standard] httpx python-dotenv jinja2 py-clob-client-v2
```

## Configuration

Copier `.env.example` vers `.env` et modifier les parametres :

```bash
cp .env.example .env
```

### Parametres principaux

| Variable | Description | Defaut |
|----------|-------------|--------|
| `MODE` | `demo` ou `production` | `demo` |
| `DEMO_INITIAL_BALANCE` | Solde initial du wallet demo | `500.00` |
| `FIXED_AMOUNT_PER_TRADE` | Montant USDC par trade copie | `5.00` |
| `MAX_DAILY_SPEND` | Budget max par jour | `100.00` |
| `POLL_INTERVAL_SECONDS` | Frequence de polling | `5` |
| `MAX_COPY_DELAY_SECONDS` | Delai max pour copier un trade | `30` |

### Pour le mode Production

```env
MODE=production
PRIVATE_KEY=0x...votre_cle_privee...
WALLET_ADDRESS=0x...votre_adresse...

# 0 = EOA, 1 = proxy Polymarket (le plus courant), 2 = Gnosis Safe
SIGNATURE_TYPE=1
```

**Important** : si vous avez cree votre compte via polymarket.com, vos USDC sont sur un proxy. Utilisez `SIGNATURE_TYPE=1` et mettez l'adresse du proxy dans `WALLET_ADDRESS` (pas votre EOA MetaMask).

### Securiser le dashboard

Des que le bot est expose publiquement (DigitalOcean), activez l'auth HTTP Basic :

```env
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=un_mot_de_passe_solide
```

Sans `DASHBOARD_PASSWORD`, l'auth est desactivee (dev local uniquement).

## Lancement

```bash
python run.py
```

Le dashboard est accessible sur **http://localhost:8000**.

**Important :** depuis cette version, le bot **ne demarre PAS automatiquement**. Il faut cliquer sur le bouton **Start** (en haut a droite du dashboard) pour lancer la surveillance et la copie de trades. Cliquez sur **Stop** pour l'arreter sans tuer le serveur.

## Deploiement (DigitalOcean)

Voir [`DEPLOY_DIGITAL_OCEAN.md`](DEPLOY_DIGITAL_OCEAN.md) pour 3 options detaillees : App Platform, Droplet+Docker, Droplet+systemd.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard web |
| `POST /api/start` | Demarrer le bot |
| `POST /api/stop` | Arreter le bot |
| `GET /api/status` | Etat actuel (running / mode / uptime) |
| `GET /api/stats` | Statistiques du bot |
| `GET /api/trades` | Historique des trades copies |
| `GET /api/positions` | Positions ouvertes |
| `GET /api/target/activity` | Activite recente de claude7 |
| `GET /api/config` | Configuration actuelle |
| `POST /api/config` | Modifier la configuration |
| `GET /api/pnl-history` | Historique P&L pour graphiques |

## Architecture

```
polymarket-copybot/
├── backend/
│   ├── app.py          # FastAPI app + endpoints
│   ├── config.py       # Configuration
│   ├── models.py       # Data models
│   ├── monitor.py      # Surveillance des trades de claude7
│   └── copy_engine.py  # Moteur de copy-trading (demo + prod)
├── frontend/
│   └── index.html      # Dashboard web
├── .env.example        # Template de configuration
├── run.py              # Point d'entree
└── README.md
```
