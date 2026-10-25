#!/usr/bin/env bash
# Rückfall nach dem pgvector-Umzug (scripts/db-migrate-pgvector.sh): db wieder mit postgres:17-alpine auf dem
# ALTEN Volume db-data betreiben. Daten, die seit dem Umzug geschrieben wurden, sind dort NICHT enthalten.
#
# Umsetzung ohne Eingriff in docker-compose.yml: das Skript schreibt docker-compose.override.yml (wird von
# `docker compose` automatisch gelesen, steht in .gitignore). Rückgängig machen = Datei löschen und
# `docker compose up -d db backend worker mcp`.
#
# Optionen:
#   --dump-first   vorher den aktuellen Stand der pgvector-DB nach ~/vision-guard-backups/ sichern (empfohlen)
#
# Die Funktionen von Welle 3, die pgvector brauchen (B1 Zeitmaschine), schalten sich auf der alten DB selbst ab
# (Backend loggt „pgvector nicht verfügbar“); alles andere läuft weiter.
# Keine Zugangsdaten im Skript: pg_dump läuft im Container über den lokalen Socket.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
OLD_IMAGE="${VG_DB_OLD_IMAGE:-postgres:17-alpine}"
OLD_VOL="${PROJECT}_db-data"
BACKUP_DIR="${VG_BACKUP_DIR:-$HOME/vision-guard-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OVERRIDE="docker-compose.override.yml"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if ! docker volume inspect "$OLD_VOL" >/dev/null 2>&1; then
  echo "Altes Volume $OLD_VOL fehlt – Rückfall nicht möglich. Backups: $BACKUP_DIR" >&2
  exit 1
fi
if [[ -e "$OVERRIDE" ]] && ! grep -q "db-rollback-pgvector" "$OVERRIDE"; then
  echo "$OVERRIDE existiert bereits mit fremdem Inhalt – bitte selbst zusammenführen. Abbruch." >&2
  exit 1
fi

if [[ "${1:-}" == "--dump-first" ]]; then
  umask 077
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  DUMP="$BACKUP_DIR/visionguard-${STAMP}-pgvector-vor-rueckfall.dump"
  log "aktuellen Stand sichern → $DUMP"
  docker compose exec -T db pg_dump -U vg -d visionguard -Fc > "$DUMP"
  chmod 600 "$DUMP"
fi

log "Dienste stoppen (backend, worker, mcp, db)"
docker compose stop backend worker mcp db

log "$OVERRIDE schreiben (db: $OLD_IMAGE auf Volume db-data)"
cat > "$OVERRIDE" <<EOF
# Erzeugt von scripts/db-rollback-pgvector.sh am $(date -Iseconds).
# Rückfall auf die Datenbank vor dem pgvector-Umzug. Löschen = wieder pgvector (db-data-pgv).
services:
  db:
    image: $OLD_IMAGE
    volumes:
      - db-data:/var/lib/postgresql/data
EOF

log "db, backend, worker, mcp starten"
docker compose up -d db backend worker mcp
log "fertig. db läuft wieder auf $OLD_VOL ($OLD_IMAGE). Rückgängig: rm $OVERRIDE && docker compose up -d db backend worker mcp"
