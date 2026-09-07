#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/Nexus-Bot-Dev"
DB="$ROOT/nexus.db"
DEST="$ROOT/backups"
TAG="${1:-scheduled}"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="$DEST/nexus-$STAMP-$TAG.db"

mkdir -p "$DEST"

# .backup uses the SQLite backup API and is safe on a live DB.
# Plain `cp` can capture a torn page mid-write.
sqlite3 "$DB" ".backup '$OUT'"

if ! sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -q '^ok$'; then
    echo "FAIL integrity_check: $OUT" >&2
    mv "$OUT" "$OUT.corrupt"
    exit 1
fi

ROWS=$(sqlite3 "$OUT" "SELECT COUNT(*) FROM users;")
if [ "$ROWS" -lt 10 ]; then
    echo "FAIL suspicious row count ($ROWS): $OUT" >&2
    mv "$OUT" "$OUT.suspicious"
    exit 1
fi

NPSUM=$(sqlite3 "$OUT" "SELECT COALESCE(SUM(np),0) FROM users;")
gzip -9 "$OUT"
echo "OK $(basename "$OUT").gz users=$ROWS np_total=$NPSUM"

/usr/bin/python3 "$ROOT/scripts/prune_backups.py"
