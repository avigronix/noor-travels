#!/usr/bin/env bash
# Nightly backup (SRS 7): database (pg_dump custom format) + uploaded photos, kept 30 days.
# Run as the 'noor' user from cron (deploy/crontab.txt). Restore: pg_restore -d noor_travels --clean FILE.dump
set -euo pipefail

APP_DIR=/srv/noor-travels
BACKUP_DIR=/srv/noor-travels-backups
KEEP_DAYS=30
STAMP=$(date +%Y-%m-%d_%H%M)

# DATABASE_URL from the app's .env (only that line is read)
DATABASE_URL=$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | cut -d= -f2-)
UPLOAD_DIR=$(grep -E '^UPLOAD_DIR=' "$APP_DIR/.env" | cut -d= -f2- || true)
UPLOAD_DIR=${UPLOAD_DIR:-uploads}
case "$UPLOAD_DIR" in /*) ;; *) UPLOAD_DIR="$APP_DIR/$UPLOAD_DIR" ;; esac

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" --file="$BACKUP_DIR/db_$STAMP.dump"
if [ -d "$UPLOAD_DIR" ]; then
  tar -czf "$BACKUP_DIR/uploads_$STAMP.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")"
fi

# keep the last 30 days only (only our own backup files in our own folder)
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db_*.dump' -o -name 'uploads_*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') backup ok: db_$STAMP.dump"
