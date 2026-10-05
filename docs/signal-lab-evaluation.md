# Évaluation observationnelle — Signal Lab

Mesure **ex post**, en continu et sans aucune position, de ce que valent les
signaux Signal Lab : pour chaque signal (BUY/SELL) et chaque horizon fixe
(5 / 15 / 30 / 60 minutes), on compare le prix d'entrée du signal au prix
constaté à l'horizon, avec les excursions maximales favorables/défavorables
(MFE/MAE) sur la fenêtre. **Aucun trade réel, aucune position simulée gérée :
c'est de la mesure, pas une stratégie.**

## Définitions

- **T_entry** : timestamp du close de la bougie déclencheuse
  (`candle_ts` + durée du timeframe). Colonne `entry_timestamp`.
- **P_entry** : `entry_price` = close de la bougie déclencheuse, figé à
  l'INSERT (jamais réécrit).
- **Fenêtre d'évaluation** : bougies 1m avec `open_ms >= T_entry`
  (anti look-ahead strict : la bougie qui contient T_entry est exclue car
  partiellement antérieure). T_end = T_entry + horizon.
- **P_exit** : close de la dernière bougie 1m avec `open_ms < T_end`
  (si le worker passe en retard, on utilise T_end, jamais le prix courant).
- **return** : `(P_exit − P_entry) / P_entry × direction`, en %.
- **direction_correct** : `return > 0` (profit brut > 0, avant coûts).
- **MFE** : excursion maximale favorable sur la fenêtre, en %.
- **MAE** : excursion maximale défavorable sur la fenêtre, en %.
- **return_net** : `return − coûts`, coûts = 20 bps par défaut
  (`signal_lab_eval_cost_bps`, 10 bps par jambe).
- **Paper P&L** : `return_net × stake` (`signal_lab_eval_paper_stake_usdt`,
  défaut 100 USDT). Calculé **à la lecture uniquement**, jamais stocké.
- **État d'évaluation** d'un signal : `UNAVAILABLE` (pas de T_entry, exclu des
  stats), `PENDING` (en attente de l'horizon), `COMPLETED`, `ERROR`
  (données 1m irrécupérables après N tentatives — exclu des stats).

## Deux méthodologies coexistent (ne pas confondre)

| | Résolution existante | Évaluation observationnelle |
|---|---|---|
| Question | Le SL/TP ATR a-t-il été touché ? | Que valait le signal à horizon fixe ? |
| Horizon | 3 bougies du timeframe du signal | 5 / 15 / 30 / 60 min fixes |
| Sortie | `outcome` win/loss, `exit_price` | `return`, `MFE`, `MAE`, `direction_correct` |
| Table | `signal_lab_signals` (UPDATE) | `signal_lab_evaluations` (lignes dédiées) |
| Label UI | « résolution 3 bougies (SL/TP ATR) » | « évaluation observationnelle (horizons fixes) » |

## Décisions

1. **Entry = close de la bougie déclencheuse.** `entry_price` existait déjà
   (close de la dernière bougie clôturée à la génération) ; on n'ajoute que
   `entry_timestamp` (= `candle_ts` + durée du timeframe) et
   `entry_price_source` (`'candle_close'`). Jamais d'invention : sans
   `candle_ts`, `entry_timestamp` reste NULL → UNAVAILABLE.
2. **Fraîcheur relative au timeframe, pas de seuil absolu.** Un signal 1h
   évalué sur des bougies 1m n'a pas besoin d'un T_entry vieux de 30 s ;
   la fenêtre démarre à T_entry quel que soit son âge, tant que les bougies
   1m couvrent [T_entry, T_end].
3. **NEUTRAL exclu** : pas de direction, return non défini. Documenté, jamais
   évalué.
4. **Trigger compatible avec le resolveur.** Le trigger d'immuabilité bloque
   l'UPDATE des colonnes historiques (`entry_price`, `entry_timestamp`,
   `entry_price_source`, `created_at`, `symbol`, `timeframe`, `direction`,
   `score`, `origin`) mais autorise les colonnes du resolveur (`outcome`,
   `exit_price`, `sl_hit`, `tp_hit`, `resolved_at`). `entry_timestamp` /
   `entry_price_source` admettent une transition unique NULL → valeur
   (backfill idempotent de `schema.sql`), puis sont immuables : verrou
   anti look-ahead en base.
5. **Coûts et P&L à la lecture uniquement**, tag `SIMULATED` obligatoire dans
   l'UI et le disclaimer API. Rien de monétaire n'est persisté.
6. **Bougies 1m à la demande** via `fetch_klines(..., "1m")`, lecture seule ;
   le pipeline de collecte (5m/1h/4h/1d) est inchangé. En retard, le worker
   exige des bougies jusqu'à T_end (pas de prix courant).
7. **Échantillon honnête** : `sample_quality` (`insufficient` < 30,
   `limited` < 100, `adequate` sinon), avertissement si timeframes mélangés,
   UNAVAILABLE/ERROR exclus des stats et comptés à part.

### Décisions actées le 2026-10-05 (revue spec v2 / déployé)

- **#1 — Bougies 1m à la demande conservées.** Le worker récupère les
  bougies 1m via le même client `fetch_klines`, la même base URL
  (`https://data-api.binance.vision`, `binance_base_url`) et le même marché
  que le collecteur : `backend/app/scheduler.py:142`
  (`binance.fetch_klines(symbol, "1m", ...)`) vs
  `backend/app/market_data/sync.py:51`
  (`binance.fetch_klines(sym, timeframe, ...)`). Le pipeline de collecte
  (5m/1h/4h/1d) est inchangé, rien n'est stocké en 1m. Équivalence prouvée
  par `test_equivalence_1m_5m` (BUY/SELL × horizons 5/15/30/60) :
  exit, return, MFE et MAE identiques entre 1m et 5m agrégées.
- **#4 — UNAVAILABLE dérivé à la lecture, conservé.** Pas de lignes
  UNAVAILABLE en base ; le statut est dérivé à la lecture via
  `signal_evaluability()`, unique source de vérité partagée par le worker
  et toute l'API. Les évaluations COMPLETED existantes ne sont jamais
  modifiées : elles sont seulement exclues des stats et leurs signaux
  comptés en unavailable (sans double comptage).
- **#6 — Alias de routes.** Le préfixe historique `/api/signal-lab/eval/*`
  est conservé ; `/api/signal-lab/evaluation/*` est ajouté en alias des
  mêmes handlers (double décorateur, aucune duplication de code).
  Test sur les deux préfixes : `test_alias_prefix_evaluation`.

## Limites connues

- Pas de slippage ni de profondeur de carnet : le P&L paper est optimiste.
- Biais de survie : seuls les signaux avec données 1m disponibles sont
  évalués ; les périodes de panne d'API sont exclues (ERROR), pas imputées.
- Les horizons fixes ne correspondent pas au TP/SL du signal : un signal
  « correct à 60 min » aurait pu toucher son SL à 10 min en réel.
- Corrélation des signaux proches dans le temps : les moyennes ne sont pas
  des tirages indépendants.
- `direction_correct` se juge sur le brut (> 0) ; la rentabilité se juge sur
  le net (après coûts).

## Opérations

- Activation : `SIGNAL_LAB_EVAL_ENABLED` (défaut true), horizons via
  `SIGNAL_LAB_EVAL_HORIZONS` (défaut `5,15,30,60`). Worker : job scheduler
  toutes les 60 s (`signal_lab_eval_job`), batch 500, `FOR UPDATE SKIP LOCKED`.
- Migration : `ALTER TABLE ... IF NOT EXISTS` dans `schema.sql` (pattern
  maison), exécuté à chaque démarrage. **Avant prod : `pg_dump`**, cf. rapport.
- Tables : `signal_lab_evaluations` (UNIQUE `(signal_id, horizon_minutes)`,
  trigger d'immuabilité des COMPLETED), index sur `(status, due_at)`,
  `(signal_id, status)`, `(horizon_minutes, status)`.
- API : `GET /api/signal-lab/eval/summary`, `/signals`, `/signals/{id}`,
  `/breakdown`. UI : sous-onglet « Évaluation » dans l'onglet Signal Lab.
