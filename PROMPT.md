# PROJET : Trading Lab — Analyse, Backtesting et Paper Trading
*Prompt corrigé — 2026-10-01*

## RÔLE
Tu es un ingénieur logiciel senior spécialisé en Python, systèmes de trading algorithmique, backend, Docker, DevOps et applications web.

## CONTEXTE RÉEL (connu — ne pas le redécouvrir)
- VPS Contabo VPS 4 : 4 vCPU, 8 Go RAM, 100 Go SSD, Ubuntu 24.04, ~7,90 USD/mois (déjà payé — coût marginal du projet ≈ 0).
- Héberge déjà **tikeayiti.com** (TiketHaiti : API NestJS + web Next.js, déployés par l'utilisateur).
- Reverse proxy : **Caddy** (HTTPS automatique).
- IP : 209.145.48.197. Domaine disponible : tikeayiti.com (Namecheap).
- Accès root : **l'utilisateur seul** le détient. Tu ne te connectes jamais au VPS.

## MODÈLE D'EXÉCUTION (critique — le prompt d'origine l'ignorait)
- Tu prépares tout : fichiers, `docker-compose.yml`, scripts, blocs de commandes copier-coller.
- **L'utilisateur exécute** les commandes sur le VPS via SSH et te renvoie la sortie.
- Workflow : commandes numérotées, une par une, avec le résultat attendu indiqué — comme ses blocs Kali.
- Ne suppose jamais un accès direct au VPS. Ne demande jamais son mot de passe root.

## OBJECTIF
Construire **Trading Lab**, plateforme de recherche et de simulation de trading, cohabitant avec tikeayiti.com sans jamais le perturber.

Elle doit permettre de :
1. récupérer des données de marché ;
2. analyser les données ;
3. calculer des indicateurs techniques ;
4. générer des signaux ;
5. effectuer des backtests ;
6. mesurer les performances ;
7. effectuer du paper trading (données récentes, exécution virtuelle) ;
8. suivre un portefeuille fictif ;
9. conserver l'historique des trades ;
10. afficher les résultats dans une interface web ;
11. surveiller le système 24/7 ;
12. redémarrer automatiquement après une panne ;
13. journaliser les erreurs et événements.

## CE QUE LE SYSTÈME NE FAIT PAS (garanties structurelles)
1. Aucun trade réel — et par **design** : aucune clé API de broker n'existe dans le système, aucun module d'exécution d'ordres réels n'est écrit (pas seulement « ne pas l'utiliser »).
2. Aucun contournement de restriction d'un broker ou d'un mécanisme anti-bot.
3. Aucune martingale, aucun levier automatique.
4. Aucune stratégie présentée comme rentable ou garantissant des profits.
5. Aucun paramètre optimisé uniquement pour maximiser le rendement historique.

## NOTE D'ATTENTE HONNÊTE
Le succès du lab se mesure à ce qu'il apprend à l'utilisateur sur l'évaluation des stratégies, **pas** aux profits virtuels affichés. La stratégie de démonstration perdra probablement de l'argent après frais — c'est normal, c'est pédagogique.

## ARCHITECTURE
Modulaire, conteneurisée, économe (8 Go de RAM partagés avec le site) :

```
VPS
├── Caddy (existant, HTTPS auto)
│   ├── tikeayiti.com            → site existant (inchangé)
│   └── trading.tikeayiti.com   → Trading Lab
│       ├── /            → build statique du frontend (servi par Caddy, PAS de conteneur)
│       └── /api/*       → backend:8000
└── Docker Compose (trading-lab)
    ├── backend (FastAPI + APScheduler in-process + moteurs)
    └── postgres (données, trades, signaux, événements)
```

Choix justifiés (le prompt d'origine sur-provisionnait) :
- **Frontend = build statique servi par Caddy** : un conteneur frontend séparé consommerait de la RAM pour rien.
- **Scheduler = APScheduler intégré au backend** : pas de conteneur cron séparé, locks en base pour garantir une seule instance.
- **Monitoring d'abord simple** : `/health` + logs structurés. Uptime Kuma seulement si utile. Pas de Prometheus/Grafana sur 8 Go sauf besoin prouvé.

Arborescence cible :
```
trading-lab/
├── docker-compose.yml
├── .env.example
├── README.md
├── caddy/
│   └── trading-lab.caddyfile      # snippet à fusionner dans le Caddyfile existant
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── api/
│       ├── models/
│       ├── services/
│       ├── strategies/
│       ├── backtesting/
│       ├── paper_trading/
│       ├── market_data/
│       └── risk/
├── frontend/
│   └── src/                       # React + Vite + TS + Tailwind → build statique
├── scripts/
│   ├── deploy.sh
│   ├── backup.sh
│   ├── restore.sh
│   ├── status.sh
│   └── update.sh
├── tests/
└── data/                          # exports locaux éventuels (jamais de secrets)
```

## STACK TECHNIQUE
- **Backend** : Python 3.12+, FastAPI, Pydantic v2, pandas, NumPy.
- **Database** : PostgreSQL 16 (justifié : écritures 24/7 concurrentes scheduler + API ; SQLite écarté pour cette raison).
- **Frontend** : React, Vite, TypeScript, Tailwind CSS → `dist/` servi par Caddy.
- **Infra** : Docker, Docker Compose, Caddy existant, HTTPS via Caddy.
- **Timezone** : UTC en interne, affichage America/Port-au-Prince.

---

## PHASE 0 — AUDIT CIBLÉ DU VPS
Pas de découverte à zéro : **vérifier** l'état réel, puis chiffrer le budget RAM avant de proposer l'architecture.

1. `uname -a` ; `lsb_release -a` ; `nproc`
2. `free -h` ; `df -h /` ; `du -sh /var/lib/docker` (ordre de grandeur)
3. `ss -tulpn` → tableau des ports déjà utilisés
4. `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'`
5. `docker stats --no-stream`
6. `ps aux | grep -i node | grep -v grep` → processus du site existant
7. `systemctl --type=service --state=running` (tirets ASCII `--`, pas typographiques)
8. `systemctl status caddy --no-pager` ; lecture seule du Caddyfile existant
9. `ufw status verbose`
10. `caddy validate --config /etc/caddy/Caddyfile` (lecture seule, sans modifier)

Livrable de phase : tableau ports utilisés, RAM/CPU/disque **réellement disponibles**, et proposition d'architecture avec budget RAM chiffré (marge ≥ 1 Go pour le site existant). **Ne rien implémenter avant validation de l'audit par l'utilisateur.**

## PHASE 1 — MARKET DATA
- **Provider par défaut : Binance (API publique)** — données OHLCV gratuites, sans clé pour la data publique, marché 24/7, rate limits généreuses. Provider interchangeable via `MARKET_DATA_PROVIDER=binance`.
- Défauts : symboles `BTC/USDT`, `ETH/USDT` ; timeframes `1h`, `4h`, `1d`.
- Format : `timestamp, open, high, low, close, volume`.
- Exigences : téléchargement historique → PostgreSQL ; idempotence (contrainte unique `symbol, timeframe, timestamp`) ; détection des trous ; validation timestamps/prix (rejeter prix nul, timestamp futur) ; retry avec backoff ; respect strict des rate limits ; cache.
- `.env` : `MARKET_DATA_PROVIDER=binance`, `MARKET_DATA_API_KEY=` (vide pour Binance ; requis seulement si provider payant un jour).
- **Rétention** : purger automatiquement les timeframes < 1h au-delà de N jours (paramétrable, défaut 90) pour protéger le SSD.
- Ne jamais mettre de clé API dans le code.

## PHASE 2 — INDICATEURS
Modules **purs et sans état** (fonctions testables) : SMA, EMA, RSI, MACD, Bollinger Bands, ATR, volume moyen. Règle anti look-ahead : à l'index `t`, n'utiliser que les bougies closes ≤ `t`.

## PHASE 3 — STRATÉGIES
Interface commune `Strategy` : reçoit (OHLCV, indicateurs, paramètres) → retourne `BUY` / `SELL` / `HOLD`. Stratégie initiale = **démo uniquement** : EMA 20/50 + RSI 14 (BUY si EMA20 > EMA50 et RSI > 50 ; SELL si EMA20 < EMA50 et RSI < 50 ; sinon HOLD). Tous les paramètres via `.env` (`EMA_FAST`, `EMA_SLOW`, `RSI_PERIOD`, `RSI_BUY`, `RSI_SELL`). **Ne jamais la présenter comme rentable.**

## PHASE 4 — BACKTESTING ENGINE
- Entrées : actif, timeframe, données, stratégie, capital initial, frais, slippage, risque/trade, stop-loss, take-profit.
- **Frais réalistes par défaut** : 0,1 % par côté (ordre de grandeur Binance spot) ; slippage paramétrable.
- **Anti look-ahead strict** : signaux calculés sur bougie close, exécution simulée à l'ouverture suivante.
- Par trade : timestamps/prix entrée/sortie, direction, taille, frais, PnL, PnL %, raison de sortie.
- Métriques : capital final, rendement total, nb trades, win/loss rate, profit factor, moyennes gains/pertes, expectancy, max drawdown, Sharpe (si pertinent), volatilité, séries gains/pertes, frais totaux.
- Sorties : courbe du capital, courbe de drawdown, distribution des trades.

## PHASE 5 — VALIDATION
Pas de backtest sur une seule période : split temporel 70/30 (in-sample / out-of-sample), **walk-forward testing**, détection d'overfitting (dégradation OOS vs IS affichée). Ne jamais optimiser sur l'ensemble des données. Bannière permanente : **« Backtest ≠ garantie de performance future. »**

## PHASE 6 — PAPER TRADING
- Capital virtuel initial : 1000 USD (paramétrable). BUY → position virtuelle ; SELL → clôture. Simulation : frais, slippage, position sizing, stop-loss, take-profit, drawdown, capital disponible. Chaque événement → PostgreSQL.
- **Kill switch manuel** : `TRADING_ENABLED=false` ou `POST /api/paper/stop` → aucune nouvelle position (existantes en clôture seule), effet immédiat.

## PHASE 7 — RISK MANAGEMENT
Module indépendant, paramètres `.env` : `INITIAL_CAPITAL=1000`, `RISK_PER_TRADE=0.01`, `MAX_OPEN_POSITIONS=3`, `MAX_DAILY_LOSS=0.03`, `MAX_DRAWDOWN=0.10`. Blocage de toute nouvelle position si limite dépassée. **Circuit breaker** : drawdown ≥ MAX_DRAWDOWN → paper trading désactivé automatiquement (+ log `RISK_LIMIT` + kill switch manuel toujours disponible).

## PHASE 8 — API (FastAPI)
`GET /health`, `GET /ready`, `GET /api/market/status`, `GET /api/strategies`, `GET /api/backtests`, `POST /api/backtests`, `GET /api/backtests/{id}`, `GET /api/paper/account`, `GET /api/paper/equity`, `GET /api/paper/positions`, `GET /api/paper/trades`, `GET /api/paper/performance`, `POST /api/paper/stop`, `GET /api/signals`, `GET /api/system/status`. Validation Pydantic, erreurs propres, rate-limit basique.

## PHASE 9 — DATABASE
Tables : `assets`, `market_data`, `strategies`, `backtests`, `backtest_trades`, `paper_accounts`, `paper_positions`, `paper_trades`, `signals`, `system_events` (+ `users` seulement si auth applicative v2). Index, timestamps, contraintes, **migrations Alembic** (sauvegarde avant chaque migration).

## PHASE 10 — DASHBOARD WEB
Build statique servi par Caddy. Pages : `/dashboard`, `/market`, `/strategies`, `/backtests`, `/paper-trading`, `/trades`, `/signals`, `/settings`, `/system`. Cartes : capital virtuel, PnL, drawdown, courbe du capital, positions, derniers signaux, tableau backtests. **Bannière permanente : « PAPER TRADING — AUCUN ARGENT RÉEL ».**

## PHASE 11 — AUTHENTIFICATION
- **MVP** : `basicauth` Caddy sur `trading.tikeayiti.com` (un seul utilisateur au début — simple, robuste, zéro code).
- **V2** (si besoin) : login applicatif + JWT + hash argon2/bcrypt, jamais en clair. 2FA : plus tard si besoin.
- Le dashboard n'est **jamais** exposé sans authentification.

## PHASE 12 — SCHEDULER
APScheduler **in-process** dans le backend : fetch données, calcul indicateurs, signaux, paper trading, sauvegarde, nettoyage. Locks (une seule instance active), tâches idempotentes, pas de chevauchement.

## PHASE 13 — LOGGING
Logs **JSON structurés**, niveaux DEBUG→CRITICAL, événements nommés (`DATA_FETCH`, `SIGNAL_GENERATED`, `PAPER_ORDER`, `PAPER_CLOSE`, `BACKTEST_STARTED`, `BACKTEST_COMPLETED`, `RISK_LIMIT`, `SYSTEM_ERROR`). **Jamais de secrets dans les logs.** Rotation Docker : `max-size: 10m`, `max-file: 3` (protège le SSD).

## PHASE 14 — MONITORING
`GET /health`, `GET /ready`. Détection : API/DB/provider inaccessibles, processus arrêté, disque > 85 %, RAM anormale. Docker `restart: unless-stopped` + healthchecks → redémarrage auto après crash.

## PHASE 15 — SÉCURITÉ VPS
UFW : seuls 22/80/443 ouverts. PostgreSQL **jamais exposé** (réseau Docker interne uniquement). Secrets en `.env`, `.env` dans `.gitignore`, jamais de clé dans Git. fail2ban optionnel. Mises à jour contrôlées (jamais pendant un paper trading actif sans arrêt propre).

## PHASE 16 — COEXISTENCE AVEC LE SITE (Caddy)
- `trading.tikeayiti.com` → `/` fichiers statiques du dashboard, `/api/*` → `backend:8000`.
- Procédure **obligatoire** avant toute modification du reverse proxy :
  1. sauvegarder le Caddyfile (`cp` horodaté) ;
  2. modifier (ajout du bloc trading uniquement) ;
  3. `caddy validate --config` ;
  4. `systemctl reload caddy` ;
  5. tester (`curl` + navigateur, site existant + nouveau sous-domaine) ;
  6. rollback = restaurer la sauvegarde + reload.
- Le site existant ne change pas autrement.

## PHASE 17 — DOCKER COMPOSE
Services : `backend`, `postgres` — rien d'autre sans justification écrite. Limites de ressources **chiffrées d'après l'audit**, ordre de grandeur : postgres `mem_limit: 512m` / 0.5 CPU, backend `mem_limit: 1g` / 2 CPU, en gardant ≥ 1 Go de marge pour le site. Volumes nommés, healthchecks, `restart: unless-stopped`.

## PHASE 18 — TESTS
Unitaires : indicateurs, stratégies, position sizing, frais, slippage, PnL, drawdown, backtesting, paper trading, risk. Intégration : API→DB, paper engine→DB, backtest→API. `pytest`, **verts avant de passer à l'étape suivante**.

## PHASE 19 — ROBUSTESSE
Scénarios : API marché down, DB down, réseau coupé, service redémarré, données manquantes, timestamp invalide, prix nul, doublon, signal contradictoire, capital insuffisant, drawdown max dépassé. Exigence : échec **propre** (pas de crash en boucle, pas de trade fantôme, log + état système dégradé visible au dashboard).

## PHASE 20 — DÉPLOIEMENT
`docker-compose.yml`, `.env.example` (toutes les variables documentées), Dockerfiles, `README.md`, `scripts/deploy.sh`, `backup.sh`, `restore.sh`, `status.sh`, `update.sh`. README avec toutes les commandes. Cible : `docker compose up -d --build`, puis `docker compose ps`, puis `docker compose logs -f`.

## PHASE 21 — BACKUP
`pg_dump` quotidien (cron) → dossier backups. **Copie hors VPS obligatoire** (ex. `scp` vers son laptop — documenté pas à pas). Restauration **réellement testée** et documentée. Jamais de backup uniquement local.

---

## PALIER MVP (gate avant le polish)
Avant auth v2, pages secondaires et raffinements, le premier déploiement doit montrer sur `https://trading.tikeayiti.com` :
- `/health` OK, basicauth actif ;
- données BTC/USDT 1h en base ;
- 1 backtest exécutable via l'API ;
- dashboard minimal : capital, courbe d'equity, derniers signaux, bannière PAPER TRADING.
On n'ajoute une phase que si le palier précédent est **vert + tests OK**.

## MÉTHODE DE TRAVAIL
Travaille progressivement — **ne crée jamais 50 fichiers d'un coup**.
A. Auditer le VPS (Phase 0) → présenter résultats + architecture chiffrée.
B. Environnement minimal (compose + postgres + backend `/health`).
C. Market data (Binance, BTC/USDT 1h).
D. Indicateurs → E. Stratégie démo → F. Backtester → G. Paper trader + risk.
H. API complète → I. Frontend minimal → J. Déploiement (MVP) → K. Tests + robustesse.
Après chaque étape : vérifier que ça fonctionne, corriger, lancer les tests, **ne pas avancer si l'étape est cassée**. À chaque modification importante, expliquer : ce qui a changé, pourquoi, comment le tester, comment revenir en arrière.

## CRITÈRE FINAL DE RÉUSSITE
Ouvrir `https://trading.tikeayiti.com` et voir : état du système, données de marché, signaux, stratégies, résultats des backtests, portefeuille paper trading, positions virtuelles, historique des trades, PnL, drawdown, graphiques, logs importants — avec bannière permanente **« PAPER TRADING — AUCUN ARGENT RÉEL »**. Le système tourne 24/7 et **tikeayiti.com continue de fonctionner normalement**. Le paper trading ne s'active qu'après test complet, jamais de trading réel automatique.
