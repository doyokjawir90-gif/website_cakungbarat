#!/bin/sh
# Cadangan database + file unggahan. Jadwalkan dengan cron, mis. setiap hari 02.00:
#   0 2 * * * /opt/cakung-barat/backup.sh
DATA_DIR="${DATA_DIR:-/opt/cakung-barat/data}"
OUT="${BACKUP_DIR:-/var/backups/cakung-barat}"
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)
python3 - "$DATA_DIR/cakung_barat.db" "$OUT/db-$STAMP.sqlite" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
tar -czf "$OUT/uploads-$STAMP.tar.gz" -C "$DATA_DIR" uploads
find "$OUT" -type f -mtime +30 -delete   # simpan 30 hari
echo "Cadangan tersimpan di $OUT"
