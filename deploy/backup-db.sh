#!/usr/bin/env bash
# Ежедневный снимок базы todobot.
#
# В bot.db реестр задач, связи с календарём, напоминания и состояния
# диалогов. Снимок делается через sqlite3 backup API: он консистентен и при
# работающем боте, в отличие от cp файла посреди записи. sqlite3 CLI на
# vps4 нет, поэтому через системный python3.
#
# Ставится в cron на сервере:
#   30 4 * * * /root/todobot/deploy/backup-db.sh >> /var/log/todobot-backup.log 2>&1

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$HERE/data/bot.db"
BACKUP_DIR="$HERE/backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M)"
FILE="$BACKUP_DIR/bot-$STAMP.db"

python3 - "$DB" "$FILE" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
if ok != "ok":
    sys.exit(f"integrity_check: {ok}")
PY
gzip -9 "$FILE"
chmod 600 "$FILE.gz"

echo "$(date '+%Y-%m-%d %H:%M') снимок готов: $FILE.gz ($(du -h "$FILE.gz" | cut -f1))"
find "$BACKUP_DIR" -name 'bot-*.db.gz' -mtime +$KEEP_DAYS -delete
echo "Снимков в хранилище: $(find "$BACKUP_DIR" -name 'bot-*.db.gz' | wc -l)"
