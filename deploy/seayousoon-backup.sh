#!/bin/sh
# Nightly backup of the Sea You Soon database (accounts, links, messages).
# Installed as /etc/cron.daily/seayousoon-backup on the VPS.
# sqlite3 .backup is safe against a live writer (unlike plain cp).
set -eu

DB=/home/seayousoon/data/data.db
DEST=/home/seayousoon/backups
KEEP_DAYS=14

mkdir -p "$DEST"
STAMP=$(date +%Y%m%d)
sqlite3 "$DB" ".backup '$DEST/data-$STAMP.db'"
gzip -f "$DEST/data-$STAMP.db"
chown -R seayousoon:seayousoon "$DEST"

find "$DEST" -name 'data-*.db.gz' -mtime +$KEEP_DAYS -delete
