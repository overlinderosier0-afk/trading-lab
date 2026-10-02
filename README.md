# Trading Lab

Plateforme de recherche et simulation de trading (analyse, backtesting, paper trading).
**Aucun trade réel — par design, aucune clé de broker n'existe dans ce système.**

## État : étape L — marquage honnête + sauvegardes

- Stratégie démo marquée `NO_EDGE_DEMONSTRATED` (acté après IS/OOS + walk-forward
  réels du 2026-10-02 : aucun edge) : badge « Aucune preuve d'edge démontré —
  stratégie de démonstration uniquement » dans les onglets Backtests et Validation.
  `GET /api/strategies` expose `edge_status` / `edge_note`.
- Sauvegardes : `scripts/backup.sh` (dump quotidien cron 03:30 heure VPS, horodaté,
  vérifié, rétention 14 j, journalisé) et `scripts/restore_test.sh` (test de
  restauration en base temporaire, comparaison des compteurs, sans toucher la prod).
  Procédures documentées ci-dessous (backup, restauration, copie hors VPS via scp,
  rétention, vérification, urgence).

## État : étape K — validation anti sur-optimisation (IS/OOS + walk-forward)

`POST /api/validation/is-oos` (découpe IS/OOS, grille optimisée sur IS puis
évaluée sur OOS jamais vu, verdict : robuste / dégradé / sur-optimisé),
`POST /api/validation/walk-forward` (plis roulants : grille optimisée par pli
TRAIN, évaluée sur TEST), `GET /api/validation/runs`,
`GET /api/validation/runs/{id}`. Table `validation_runs`. Tests :
`backend/tests/` (18 tests : géométrie des plis, no-lookahead avec données
futures, sélection Sharpe, garde-fous de grille, câblage API, régression
`/health` avant le catch-all SPA). Nouvel onglet dashboard « Validation ».
Fix : `/health` et `/ready` enregistrés AVANT le catch-all SPA (avant, le
catch-all interceptait `/health` et retournait index.html).

## État : étape G — paper trading + risk + scheduler

Moteur de paper trading **virtuel uniquement** (aucun ordre réel possible par design) :
cycle toutes les 15 min sur la dernière bougie close (sync incrémental, signal,
stop/take, application du signal après garde-fous).
Risk management indépendant : kill switch manuel, activation explicite requise
(`POST /api/paper/start`, défaut OFF), max positions, perte journalière max,
circuit breaker drawdown (désactivation auto).
Scheduler APScheduler in-process : sync données toutes les 30 min, paper toutes
les 15 min. Tables : `paper_accounts`, `paper_positions`, `paper_trades`,
`signals`, `system_events`, `system_state`.
Endpoints : `/api/paper/*` (account, positions, trades, performance, signals,
start, stop), `/api/system/*` (status, events).

## Historique des étapes

- **F** — backtesting : `POST /api/backtests` (simulation barre par barre,
  frais+slippage, sizing risque), `GET /api/backtests`, `GET /api/backtests/{id}`.
- **C** — market data : `POST /api/market/sync` (backfill + incrémental Binance),
  `GET /api/market/status`, `GET /api/market/candles`.

- **C** — market data : `POST /api/market/sync` (backfill + incrémental Binance),
  `GET /api/market/status`, `GET /api/market/candles`.
- **D/E** — indicateurs (SMA, EMA, RSI, MACD, Bollinger, ATR, volume moyen) +
  stratégie démo `ema_rsi_demo` ; `GET /api/strategies`, `GET /api/signals`.

## Mise à jour du code (étapes suivantes)

L'archive ne contient jamais `.env` (ton mot de passe est en sécurité) :
```bash
cd ~
curl -L -o trading-lab.tar.gz '<URL>'
tar -xzf trading-lab.tar.gz -C trading-lab --strip-components=1 && rm trading-lab.tar.gz
cd ~/trading-lab
docker compose up -d --build
```

## Test market data

```bash
# synchro complète (défauts : BTCUSDT+ETHUSDT, 1h/4h/1d, 365 jours) — ~1 min
docker exec trading-lab-backend curl -sf -X POST http://localhost:8000/api/market/sync \
  -H 'Content-Type: application/json' -d '{}'
# état des données
docker exec trading-lab-backend curl -sf http://localhost:8000/api/market/status
# dernières bougies
docker exec trading-lab-backend curl -sf 'http://localhost:8000/api/market/candles?symbol=BTCUSDT&timeframe=1h&limit=3'
```

## Démarrage

```bash
cd ~/trading-lab
cp .env.example .env
# génère un mot de passe sûr et l'injecte aux 2 endroits :
PASS=$(openssl rand -hex 24) && sed -i "s/change-me-generate-strong/$PASS/g" .env
docker compose up -d --build
docker compose ps
docker exec trading-lab-backend curl -sf http://localhost:8000/health
docker exec trading-lab-backend curl -sf http://localhost:8000/ready
```

## Commandes utiles

```bash
docker compose ps                  # état des services
docker compose logs -f backend     # logs temps réel (JSON structurés)
docker compose logs --tail=50 postgres
docker compose down                # tout arrêter (données pgdata conservées)
docker compose down -v             # tout arrêter + SUPPRIMER les données (irréversible)
```

## Rollback étape B

```bash
cd ~/trading-lab && docker compose down -v && cd ~ && rm -rf ~/trading-lab
```

Le VPS revient exactement à son état d'avant.

## Sauvegardes PostgreSQL (étape L)

### Principe
- Dump quotidien automatique via cron (script `scripts/backup.sh`).
- Fichiers horodatés : `~/trading-lab/backups/trading-lab-AAAAMMJJ-HHMMSS.sql.gz`.
- Chaque dump est **vérifié** (existe, non vide, `gzip -t` OK) avant d'être compté comme réussi.
- Succès/échec journalisé dans `~/trading-lab/backups/backup.log` (+ ligne `BACKUP_OK`
  dans `system_events`, visible dans l'onglet Système).
- Le mot de passe est lu dans `~/trading-lab/.env` à l'exécution — jamais dans
  les logs, jamais dans Git, jamais dans les archives.

### Installation (une fois)
```bash
mkdir -p ~/trading-lab/backups
chmod +x ~/trading-lab/scripts/backup.sh ~/trading-lab/scripts/restore_test.sh
# backup manuel immédiat (vérifie que tout fonctionne)
~/trading-lab/scripts/backup.sh && tail -3 ~/trading-lab/backups/backup.log && ls -la ~/trading-lab/backups/
# automatisation quotidienne à 03:30 (heure du VPS, Europe/Berlin)
(crontab -l 2>/dev/null; echo "30 3 * * * /root/trading-lab/scripts/backup.sh") | crontab -
crontab -l | grep backup
```

### Politique de rétention
- Conservation : **14 jours** (`RETENTION_DAYS`, modifiable en tête de `scripts/backup.sh`
  ou via variable d'environnement).
- Seuls les fichiers `trading-lab-*.sql.gz` plus vieux que la rétention sont supprimés,
  et chaque suppression est journalisée (`RETENTION supprimé ...`).
- **Aucune suppression n'a lieu sans cette politique explicite** : le script ne touche
  à aucun autre fichier du dossier.

### Vérification
```bash
tail -5 ~/trading-lab/backups/backup.log   # derniers OK / FAIL
ls -lat ~/trading-lab/backups/*.sql.gz | head -5
gzip -t ~/trading-lab/backups/trading-lab-<le-dernier>.sql.gz && echo "archive intègre"
```

## Copie hors VPS (obligatoire — le VPS n'est pas la seule copie)

Depuis ton ordinateur (pas d'envoi automatique vers un service externe pour l'instant) :
```bash
mkdir -p ~/backups-trading-lab
scp root@209.145.48.197:~/trading-lab/backups/'trading-lab-*.sql.gz' ~/backups-trading-lab/
ls -la ~/backups-trading-lab/
```
Recommandé : une fois par semaine, après avoir vérifié `backup.log`.

## Restauration — test (sans toucher à la production)

```bash
~/trading-lab/scripts/restore_test.sh ~/trading-lab/backups/trading-lab-<le-dernier>.sql.gz
```
Le script crée une base **temporaire** `tradinglab_restore_test`, y restaure le dump,
compare le nombre de lignes par table (prod vs restaurée) et affiche le résultat.
Un écart sur `system_events` est normal (le backup journalise lui-même).
**Le script ne supprime rien** : il affiche la commande exacte de nettoyage
(`DROP DATABASE tradinglab_restore_test;`) à exécuter manuellement après vérification.

## Restauration — urgence (perte de données en production)

⚠️ À n'exécuter qu'après validation explicite. Montre d'abord la commande et son effet.
```bash
# 1. Arrêter ce qui écrit en base (backend + scheduler)
cd ~/trading-lab && docker compose stop backend
# 2. Restaurer le dump choisi dans la base de production
gunzip -c ~/trading-lab/backups/trading-lab-<dump-choisi>.sql.gz | \
  docker exec -i trading-lab-postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB> -v ON_ERROR_STOP=1
# 3. Vérifier puis redémarrer
docker exec trading-lab-postgres psql -U <POSTGRES_USER> -d <POSTGRES_DB> -c "SELECT count(*) FROM market_data;"
cd ~/trading-lab && docker compose start backend
```
Effet : **remplace** le contenu actuel de la base par le dump (les données plus
récentes que le dump sont perdues). En cas de doute : restaure d'abord dans une
base temporaire (voir section test) pour inspecter le dump.
