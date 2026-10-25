#!/usr/bin/env bash
# Umzug der Datenbank von postgres:17-alpine (Volume db-data) auf pgvector (Volume db-data-pgv) per Dump/Restore.
#
# Warum Dump/Restore statt Image-Tausch auf demselben Volume: Alpine (musl) → Debian (glibc) ändert die
# Sortierregeln; Text-Indizes wären sonst still kaputt. Das alte Volume bleibt als Rückfall unverändert
# (scripts/db-rollback-pgvector.sh).
#
# Ablauf:
#   1. backend/worker/mcp/db stoppen
#   2. altes Volume in einem Wegwerf-Container (postgres:17-alpine) starten, Zeilen zählen, pg_dump -Fc
#      nach ~/vision-guard-backups/ (Verzeichnis 700, Datei 600)
#   3. db aus docker-compose.yml (pgvector-Image, neues Volume) starten, CREATE EXTENSION vector, pg_restore
#   4. Zeilenzahlen aller Tabellen vergleichen – bei Abweichung Abbruch (Dienste bleiben gestoppt)
#   5. backend/worker/mcp wieder starten
#
# Keine Zugangsdaten im Skript: alle psql/pg_dump-Aufrufe laufen im Container über den lokalen Socket.
# Aufruf aus dem Repo-Wurzelverzeichnis:  scripts/db-migrate-pgvector.sh [--force]
#   --force  neues Volume existiert schon → trotzdem fortfahren (es wird NICHT gelöscht; Restore schlägt bei
#            vorhandenen Tabellen fehl). Zum Neuversuch das neue Volume vorher selbst löschen.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
OLD_IMAGE="${VG_DB_OLD_IMAGE:-postgres:17-alpine}"
OLD_VOL="${PROJECT}_db-data"
NEW_VOL="${PROJECT}_db-data-pgv"
DB=visionguard
DBUSER=vg
BACKUP_DIR="${VG_BACKUP_DIR:-$HOME/vision-guard-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="$BACKUP_DIR/visionguard-${STAMP}-vor-pgvector.dump"
TMP="vg-db-old-${STAMP}"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

COUNT_SQL="select table_name || ' ' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by table_name;"

if ! docker volume inspect "$OLD_VOL" >/dev/null 2>&1; then
  echo "Altes Volume $OLD_VOL nicht gefunden – nichts umzuziehen." >&2
  exit 1
fi
if docker volume inspect "$NEW_VOL" >/dev/null 2>&1 && [[ $FORCE -eq 0 ]]; then
  echo "Neues Volume $NEW_VOL existiert bereits (Umzug schon erfolgt?). Abbruch; --force zum Fortfahren." >&2
  exit 1
fi

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "Dienste stoppen (backend, worker, mcp, db)"
docker compose stop backend worker mcp db

log "altes Volume $OLD_VOL im Wegwerf-Container $OLD_IMAGE starten"
docker run -d --name "$TMP" -v "$OLD_VOL:/var/lib/postgresql/data" "$OLD_IMAGE" >/dev/null
cleanup() { docker rm -f "$TMP" >/dev/null 2>&1 || true; }
trap cleanup EXIT
for _ in $(seq 1 60); do
  docker exec "$TMP" pg_isready -U "$DBUSER" -d "$DB" >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$TMP" pg_isready -U "$DBUSER" -d "$DB" >/dev/null

docker exec "$TMP" psql -U "$DBUSER" -d "$DB" -Atc "$COUNT_SQL" > "$BACKUP_DIR/zeilen-${STAMP}-alt.txt"
log "Zeilen alt: $(wc -l < "$BACKUP_DIR/zeilen-${STAMP}-alt.txt") Tabellen"

log "pg_dump → $DUMP"
docker exec "$TMP" pg_dump -U "$DBUSER" -d "$DB" -Fc > "$DUMP"
chmod 600 "$DUMP" "$BACKUP_DIR/zeilen-${STAMP}-alt.txt"
docker exec -i "$TMP" pg_restore --list < "$DUMP" >/dev/null   # Dump lesbar?
log "Dump: $(du -h "$DUMP" | cut -f1)"
cleanup
trap - EXIT

log "neue db (pgvector, Volume $NEW_VOL) starten"
docker compose up -d db
for _ in $(seq 1 90); do
  docker compose exec -T db pg_isready -U "$DBUSER" -d "$DB" >/dev/null 2>&1 && break
  sleep 1
done
docker compose exec -T db pg_isready -U "$DBUSER" -d "$DB" >/dev/null
sleep 2   # Init-Skripte des Images (erster Start) abwarten
docker compose exec -T db pg_isready -U "$DBUSER" -d "$DB" >/dev/null

log "CREATE EXTENSION vector + pg_restore"
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$DBUSER" -d "$DB" -qc "CREATE EXTENSION IF NOT EXISTS vector"
docker compose exec -T db pg_restore -U "$DBUSER" -d "$DB" --no-owner --exit-on-error < "$DUMP"
docker compose exec -T db psql -U "$DBUSER" -d "$DB" -qc "ANALYZE" >/dev/null

docker compose exec -T db psql -U "$DBUSER" -d "$DB" -Atc "$COUNT_SQL" > "$BACKUP_DIR/zeilen-${STAMP}-neu.txt"
chmod 600 "$BACKUP_DIR/zeilen-${STAMP}-neu.txt"
if ! diff -u "$BACKUP_DIR/zeilen-${STAMP}-alt.txt" "$BACKUP_DIR/zeilen-${STAMP}-neu.txt"; then
  echo "FEHLER: Zeilenzahlen weichen ab – Dienste bleiben gestoppt. Rückfall: scripts/db-rollback-pgvector.sh" >&2
  exit 2
fi
log "Zeilenzahlen identisch ($(wc -l < "$BACKUP_DIR/zeilen-${STAMP}-neu.txt") Tabellen)"
docker compose exec -T db psql -U "$DBUSER" -d "$DB" -Atc "select 'vector ' || extversion from pg_extension where extname = 'vector'"

log "backend, worker, mcp starten"
docker compose up -d backend worker mcp
log "fertig. Backup: $DUMP – altes Volume $OLD_VOL bleibt als Rückfall erhalten."
