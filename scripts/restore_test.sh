#!/usr/bin/env bash
# Trading Lab — TEST de restauration SANS toucher à la production.
#
# Usage : ~/trading-lab/scripts/restore_test.sh ~/trading-lab/backups/trading-lab-AAAAMMJJ-HHMMSS.sql.gz
#
# Ce que fait le script :
#   1. Vérifie le dump (existe + gzip -t).
#   2. Crée une base TEMPORAIRE `tradinglab_restore_test` (la prod n'est jamais touchée).
#   3. Restaure le dump dans cette base temporaire.
#   4. Compare le nombre de lignes par table : prod vs restaurée, affiche le tableau.
#   5. NE SUPPRIME RIEN : affiche la commande exacte pour nettoyer la base
#      temporaire, à exécuter manuellement après vérification.
#
# Effets de bord : une base `tradinglab_restore_test` est créée sur le postgres
# Trading Lab. Aucune écriture dans la base de production.

set -u

COMPOSE_DIR="${COMPOSE_DIR:-$HOME/trading-lab}"
ENV_FILE="$COMPOSE_DIR/.env"
CONTAINER="trading-lab-postgres"
TEST_DB="tradinglab_restore_test"
TABLES="market_data backtests validation_runs paper_accounts paper_positions paper_trades signals system_events system_state"

DUMP="${1:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "Usage : $0 <chemin-du-dump.sql.gz>"
    exit 1
fi
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "FAIL : dump illisible ($DUMP)"
    exit 1
fi

POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_USER="${POSTGRES_USER:-tradinglab}"
POSTGRES_DB="${POSTGRES_DB:-tradinglab}"

psql_exec() { # $1 = base, $2 = sql
    docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
        psql -U "$POSTGRES_USER" -d "$1" -t -A -c "$2"
}

echo "== 1. Création de la base temporaire $TEST_DB (prod intacte)"
psql_exec "$POSTGRES_DB" "DROP DATABASE IF EXISTS $TEST_DB;" >/dev/null
psql_exec "$POSTGRES_DB" "CREATE DATABASE $TEST_DB;" >/dev/null \
    || { echo "FAIL : création de $TEST_DB impossible"; exit 1; }

echo "== 2. Restauration du dump dans $TEST_DB"
if ! gunzip -c "$DUMP" | docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
        psql -U "$POSTGRES_USER" -d "$TEST_DB" -q -v ON_ERROR_STOP=1 >/dev/null 2>/tmp/restore_err.log; then
    echo "FAIL : restauration impossible :"
    cat /tmp/restore_err.log
    exit 1
fi
echo "Restauration terminée sans erreur."

echo "== 3. Comparaison prod vs restaurée"
printf '%-18s %12s %12s %s\n' "table" "prod" "restaurée" "statut"
FAIL=0
for t in $TABLES; do
    prod_n="$(psql_exec "$POSTGRES_DB" "SELECT count(*) FROM $t;" 2>/dev/null || echo ERR)"
    test_n="$(psql_exec "$TEST_DB" "SELECT count(*) FROM $t;" 2>/dev/null || echo ERR)"
    if [ "$prod_n" = "$test_n" ]; then st="OK"; else st="DIFFÈRE"; FAIL=1; fi
    printf '%-18s %12s %12s %s\n' "$t" "$prod_n" "$test_n" "$st"
done

echo
echo "Note : un écart sur system_events est NORMAL (le backup journalise"
echo "lui-même des événements BACKUP_OK après la création du dump)."
echo
if [ "$FAIL" = "0" ]; then
    echo "RÉSULTAT : restauration CONFORME (toutes les tables identiques)."
else
    echo "RÉSULTAT : écarts constatés — vérifier qu'ils s'expliquent (voir note)."
fi
echo
echo "== 4. Nettoyage (MANUEL — rien n'est supprimé par ce script)"
echo "Quand tu as vérifié le tableau, exécute pour supprimer la base temporaire :"
echo
echo "  docker exec trading-lab-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c \"DROP DATABASE $TEST_DB;\""
echo
echo "Effet : supprime UNIQUEMENT la base temporaire $TEST_DB. La production ($POSTGRES_DB) n'est pas touchée."
