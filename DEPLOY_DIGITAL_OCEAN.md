# Déployer Polymarket CopyBot sur DigitalOcean

Ce guide présente **3 options** classées de la plus simple à la plus flexible :

| # | Option | Difficulté | Coût indicatif | Reboot auto | Idéal pour |
|---|--------|------------|----------------|-------------|------------|
| 1 | **App Platform** (Docker managé) | ★ Facile | ~$5/mois (basic-xxs) | Oui | Déploiement zéro-admin, CI/CD via GitHub |
| 2 | **Droplet + Docker** | ★★ Moyen | ~$6/mois (s-1vcpu-1gb) | À configurer | Contrôle total, prix fixe |
| 3 | **Droplet + systemd** (sans Docker) | ★★ Moyen | ~$6/mois | Oui (systemd) | Installation Python "à l'ancienne" |

> Avant tout : assurez-vous d'avoir mis votre code sur GitHub (un repo privé suffit) et d'avoir un compte DigitalOcean.

---

## Option 1 — DigitalOcean App Platform (recommandé)

C'est l'option la plus simple : DO build le Docker, le déploie, le redémarre tout seul en cas de crash, gère le HTTPS, et redéploie automatiquement quand vous pushez sur `main`.

### A. Via l'interface web (5 min)

1. Connectez-vous sur https://cloud.digitalocean.com/apps et cliquez **Create App**.
2. Choisissez **GitHub** comme source, autorisez DO, sélectionnez votre repo `polymarket-copybot` et la branche `main`.
3. DO détecte automatiquement le `Dockerfile`. Laissez les valeurs par défaut.
4. **Resource size** : `Basic` → `basic-xxs` (~$5/mois). Suffisant pour le bot.
5. **HTTP port** : `8080` (déjà dans le Dockerfile).
6. **Environment variables** — ajoutez (au minimum) :
   - `MODE = demo`
   - `TARGET_USERNAME = claude7`
   - `TARGET_WALLET = 0x07480f204434ad41b1705b9d1de5bbfc451092a1`
   - `FIXED_AMOUNT_PER_TRADE = 5.00`
   - `MAX_DAILY_SPEND = 100.00`
   - `POLL_INTERVAL_SECONDS = 5`
   - `MAX_COPY_DELAY_SECONDS = 30`
   - `DEMO_INITIAL_BALANCE = 500.00`
   - Pour la production : `PRIVATE_KEY` et `WALLET_ADDRESS` → **cochez "Encrypt" (type=SECRET)**.
7. Cliquez **Create Resources**. Le build prend ~3-5 min.
8. À la fin, App Platform vous donne une URL `https://polymarket-copybot-xxxxx.ondigitalocean.app/`. Ouvrez-la, vous voyez le dashboard. Cliquez sur **Start** pour lancer le bot.

### B. Via le fichier `app.yaml` (déploiement reproductible)

Le fichier `.do/app.yaml` est déjà inclus dans le projet. Éditez le bloc `github:` pour mettre votre repo, puis :

```bash
# Installer doctl (CLI de DigitalOcean)
brew install doctl                # macOS
# OU : snap install doctl         # Linux

# Authentifier (https://cloud.digitalocean.com/account/api/tokens)
doctl auth init

# Créer l'app
doctl apps create --spec .do/app.yaml

# Lister les apps
doctl apps list

# Mettre à jour l'app
doctl apps update <APP_ID> --spec .do/app.yaml
```

À chaque `git push` sur `main`, App Platform redéploie automatiquement.

### Logs et monitoring
- Dans l'interface : onglet **Runtime Logs** de l'app.
- En CLI : `doctl apps logs <APP_ID> --type run --follow`

---

## Option 2 — Droplet + Docker

Plus de contrôle, prix fixe (pas de surprises facturation).

### 1. Créer un Droplet

1. https://cloud.digitalocean.com/droplets/new
2. Image : **Marketplace → Docker on Ubuntu 22.04** (Docker pré-installé).
3. Plan : **Basic → Regular → $6/mois (1GB / 1vCPU)**. Suffisant pour le bot.
4. Région : la plus proche de vous (ex. Frankfurt `fra1`).
5. Auth : **SSH key** (recommandé) — collez votre clé publique.
6. Hostname : `polymarket-copybot`.
7. Cliquez **Create Droplet**.

### 2. Se connecter et déployer

```bash
# Connexion (remplacez par l'IP donnée par DO)
ssh root@<IP_DU_DROPLET>

# Cloner le repo
git clone https://github.com/YOUR_USER/polymarket-copybot.git
cd polymarket-copybot

# Créer le fichier .env avec vos paramètres
cp .env.example .env
nano .env   # éditer MODE, PRIVATE_KEY si production, etc.

# Build + run en arrière-plan (avec restart auto)
docker build -t copybot .
docker run -d \
  --name copybot \
  --restart unless-stopped \
  --env-file .env \
  -p 80:8080 \
  copybot
```

Le dashboard est maintenant accessible sur `http://<IP_DU_DROPLET>/`.

### 3. Mettre à jour le bot

```bash
cd /root/polymarket-copybot
git pull
docker build -t copybot .
docker stop copybot && docker rm copybot
docker run -d --name copybot --restart unless-stopped --env-file .env -p 80:8080 copybot
```

### 4. (Optionnel) HTTPS via Caddy

Pour une URL `https://mondomaine.com` propre :

```bash
docker run -d --name caddy --restart unless-stopped \
  -p 443:443 -p 80:80 \
  -v caddy_data:/data \
  caddy:2 caddy reverse-proxy --from mondomaine.com --to localhost:8080
```

(Pointez d'abord votre domaine vers l'IP du Droplet via un record DNS A.)

### 5. Logs

```bash
docker logs -f copybot
```

---

## Option 3 — Droplet + systemd (sans Docker)

Si vous préférez Python natif.

```bash
# Sur le Droplet (Ubuntu 22.04)
apt-get update && apt-get install -y python3.11 python3.11-venv python3-pip git

# Cloner
git clone https://github.com/YOUR_USER/polymarket-copybot.git /opt/copybot
cd /opt/copybot

# Venv + deps
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt

# Config
cp .env.example .env
nano .env

# Service systemd
cat > /etc/systemd/system/copybot.service <<'EOF'
[Unit]
Description=Polymarket CopyBot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/copybot
EnvironmentFile=/opt/copybot/.env
ExecStart=/opt/copybot/venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now copybot
systemctl status copybot

# Ouvrir le port 8080 (ou rediriger 80 -> 8080 avec nginx/caddy)
ufw allow 8080/tcp
```

Le bot tourne maintenant en service. Logs : `journalctl -u copybot -f`.

---

## Sécurité : à faire AVANT la production

1. **Auth du dashboard OBLIGATOIRE** dès qu'il est public. Définissez :
   - `DASHBOARD_USER=admin`
   - `DASHBOARD_PASSWORD=<un mot de passe solide>` → marquez-le **type=SECRET** sur App Platform.
   Sans `DASHBOARD_PASSWORD`, l'app démarre avec un WARNING dans les logs et **toute la dashboard est ouverte**.
2. **Choisissez le bon `SIGNATURE_TYPE`** :
   - `1` si vous avez créé votre compte via polymarket.com (cas majoritaire, fonds sur le proxy).
   - `0` si vous tradez en direct avec votre EOA MetaMask.
   - `2` pour un Gnosis Safe.
   Mauvaise valeur = ordres rejetés en silence côté Polymarket.
3. **`WALLET_ADDRESS` doit être l'adresse qui détient l'USDC** (votre proxy Polymarket pour `SIGNATURE_TYPE=1`, votre EOA pour `0`), PAS systématiquement votre EOA MetaMask.
4. **Ne commitez JAMAIS votre `.env`** avec une vraie `PRIVATE_KEY`. Le `.gitignore` du projet l'exclut déjà.
5. Sur App Platform, déclarez `PRIVATE_KEY`, `WALLET_ADDRESS` et `DASHBOARD_PASSWORD` avec **type=SECRET** (chiffré au repos).
6. Sur un Droplet, mettez `chmod 600 .env` et créez un user dédié non-root.
7. Ajoutez un **firewall DO** qui n'autorise que les ports 22, 80, 443.
8. Commencez **TOUJOURS en `MODE=demo`** quelques jours pour valider la stratégie avant de basculer en `production`.
9. Limitez `MAX_DAILY_SPEND` à une valeur que vous pouvez perdre.

---

## Mémo des coûts

- **App Platform basic-xxs** : ~$5/mois, scaling auto, HTTPS inclus.
- **Droplet s-1vcpu-1gb** : $6/mois, prix fixe, contrôle total.
- **Bandwidth** : 500 GB/mois inclus sur les deux (largement suffisant).
- **Backups DO** : +20% sur le Droplet (recommandé en production).
