#!/usr/bin/env bash
# Nightly SQLite backup with retention. Run from cron / Task Scheduler.
#   BACKUP_DIR=/var/backups/ai-workspace DB=/data/app.db ./scripts/backup.sh
set -euo pipefail

DB="${DB:-/data/app.db}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB" ]; then
  echo "database not found at $DB" >&2
  exit 1
fi

# .backup is safe while the app is running (unlike copying a WAL-mode file).
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$BACKUP_DIR/app-$STAMP.db'"
else
  cp "$DB" "$BACKUP_DIR/app-$STAMP.db"
fi

gzip -f "$BACKUP_DIR/app-$STAMP.db"

# Verify the archive is readable before keeping it.
gzip -t "$BACKUP_DIR/app-$STAMP.db.gz"

find "$BACKUP_DIR" -name 'app-*.db.gz' -mtime "+$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/app-$STAMP.db.gz"
