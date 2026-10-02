#!/usr/bin/env bash
# Trading Lab — sauvegarde quotidienne PostgreSQL.
#
# Usage manuel :  ~/trading-lab/scripts/backup.sh
# Cron conseillé (heure du VPS, Europe/Berlin) :
#   30 3 * * * /root/trading-lab/scripts/backup.sh
#
# Ce que fait le script :
#   1. pg_dump du postgres Trading Lab -> ~/trading-lab/backups/trading-lab-AAAAMMJJ-HHMMSS.sql.gz
#   2. Vérifie que le dump existe, est non vide et passe `gzip -t` (intégrité).
#   3. Applique la rétention : supprime UNIQUEMENT les dumps `trading-lab-*.sql.gz`
#      plus vieux que RETENTION_DAYS (défaut 14), en journalisant chaque suppression.
#   4. Journalise succès/échec dans backup.log + INSERT best-effort dans
#      system_events (visible dans l'onglet Système du dashboard).
#
# Sécurité : le mot de passe est lu dans ~/trading-lab/.env à l'exécution,
# jamais écrit dans les logs ni commité (les dumps restent sur le VPS ;
# voir README pour la copie hors VPS via scp).

set -u

COMPOSE_DIR="${COMPOSE_DIR:-$HOME/trading-lab}"
ENV_FILE="$COMPOSE_DIR/.env"
BACKUP_DIR="${BACKUP_DIR:-$COMPOSE_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
LOG_FILE="$BACKUP_DIR/backup.log"
TS="$(date +%Y%m%d-%H%M%S)"
DUMP_FILE="$BACKUP_DIR/trading-lab-$TS.sql.gz"
CONTAINER="trading-lab-postgres"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG_FILE"; }

mkdir -p "$BACKUP_DIR"

if [ ! -f "$ENV_FILE" ]; then
    log "FAIL .env introuvable ($ENV_FILE)"
    exit 1
fi
# Lecture du .env sans jamais afficher les valeurs.
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_USER="${POSTGRES_USER:-tradinglab}"
POSTGRES_DB="${POSTGRES_DB:-tradinglab}"
if [ -z "${POSTGRES_PASSWORD:-}" ]; then
    log "FAIL POSTGRES_PASSWORD introuvable dans .env"
    exit 1
fi

# 1) Dump (format SQL texte, --no-owner/--no-acl pour une restauration simple).
if ! docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
        pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl 2>>"$LOG_FILE" \
        | gzip > "$DUMP_FILE"; then
    log "FAIL pg_dump a échoué"
    rm -f "$DUMP_FILE"
    exit 1
fi

# 2) Vérifications : fichier existant, non vide, archive gzip intègre.
if [ ! -s "$DUMP_FILE" ]; then
    log "FAIL dump vide ou absent ($DUMP_FILE)"
    rm -f "$DUMP_FILE"
    exit 1
fi
if ! gzip -t "$DUMP_FILE" 2>>"$LOG_FILE"; then
    log "FAIL dump corrompu (gzip -t) ($DUMP_FILE)"
    rm -f "$DUMP_FILE"
    exit 1
fi
SIZE="$(du -h "$DUMP_FILE" | cut -f1)"
log "OK $DUMP_FILE ($SIZE)"

# 3) Rétention explicite : uniquement nos dumps, plus vieux que RETENTION_DAYS.
#    Ne touche à rien d'autre dans le dossier.
find "$BACKUP_DIR" -maxdepth 1 -name 'trading-lab-*.sql.gz' \
    -mtime +"$RETENTION_DAYS" -print | while IFS= read -r old; do
    rm -f "$old" && log "RETENTION supprimé (>${RETENTION_DAYS}j) : $old"
done

# 4) Journal best-effort dans system_events (n'échoue jamais le backup).
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER" \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -c \
    "INSERT INTO system_events (level, event, message) VALUES ('INFO', 'BACKUP_OK', 'dump $TS ($SIZE), rétention ${RETENTION_DAYS}j');" \
    >>"$LOG_FILE" 2>&1 || log "WARN journal system_events impossible (backup OK quand même)"

exit 0
