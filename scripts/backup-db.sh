#!/usr/bin/env bash
# backup-db.sh — dump DB app + tar uploads ke var/backups/. Retensi default 14 hari.
#
# Dipakai sebagai jalur dump MANDIRI app ini (server backup yang ada hanya
# auto-discover WordPress, jadi app baru tidak otomatis tercakup — PRD §14).
#
#   ./scripts/backup-db.sh                # dump + retensi
#   RETENSI_HARI=30 ./scripts/backup-db.sh
#
# PENTING: salinan ke luar box (gdrivecrypt:/s3crypt:) TIDAK dilakukan di sini;
# itu bagian kebijakan backup server (paket `gamisjumbo-backup`, Level 2).

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$DIR/.env}"
RETENSI_HARI="${RETENSI_HARI:-14}"
TUJUAN="${TUJUAN:-$DIR/var/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
HOST="$(hostname -s)"

# muat .env tanpa mengeksekusi shell (hanya KEY=VALUE)
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" | sed 's/\r$//')
  set +a
fi

: "${DATABASE_URL:?DATABASE_URL tidak ada di .env}"

mkdir -p "$TUJUAN"
ARSIP="$TUJUAN/$HOST-$STAMP.tar.gz"

if [[ "$DATABASE_URL" == mysql* ]]; then
  # mysql+pymysql://user:pass@host:port/dbname?...
  creds="${DATABASE_URL#*://}"; creds="${creds%%\?*}"
  USERPASS="${creds%%@*}"; HOSTPART="${creds#*@}"
  DBUSER="${USERPASS%%:*}"; DBPASS="${USERPASS#*:}"
  DBHOSTPORT="${HOSTPART%%/*}"; DBNAME="${HOSTPART#*/}"
  DBHOST="${DBHOSTPORT%%:*}"; DBPORT="${DBHOSTPORT##*:}"
  [[ "$DBPORT" == "$DBHOST" ]] && DBPORT=3306
  echo "[backup] dump MariaDB $DBNAME dari $DBHOST:$DBPORT"

  TMPDIR_DUMP="$(mktemp -d)"
  trap 'rm -rf "$TMPDIR_DUMP"' EXIT
  MYSQL_PWD="$DBPASS" mysqldump \
      --single-transaction --quick --routines --triggers \
      --default-character-set=utf8mb4 \
      -h "$DBHOST" -P "$DBPORT" -u "$DBUSER" "$DBNAME" > "$TMPDIR_DUMP/db.sql"
  gzip -9 "$TMPDIR_DUMP/db.sql"
else
  # SQLite: cukup salin file DB
  echo "[backup] salin file SQLite"
  TMPDIR_DUMP="$(mktemp -d)"
  trap 'rm -rf "$TMPDIR_DUMP"' EXIT
  DBPATH="${DATABASE_URL#*///}"
  [[ "$DBPATH" != /* ]] && DBPATH="$DIR/${DBPATH#./}"
  sqlite3 "$DBPATH" ".backup '$TMPDIR_DUMP/db.sqlite'" 2>/dev/null || cp "$DBPATH" "$TMPDIR_DUMP/db.sqlite"
fi

# arsipkan: dump + uploads + .env (mode 600 di dalam arsip) + seed
tar -czf "$ARSIP" \
  -C "$TMPDIR_DUMP" . \
  -C "$DIR" .env seed var/uploads 2>/dev/null || true
chmod 600 "$ARSIP"

sha256sum "$ARSIP" > "$ARSIP.sha256"
echo "[backup] selesai: $ARSIP ($(du -h "$ARSIP" | cut -f1))"

# retensi
find "$TUJUAN" -maxdepth 1 -name '*.tar.gz' -mtime "+$RETENSI_HARI" -print -delete || true
find "$TUJUAN" -maxdepth 1 -name '*.sha256' -mtime "+$RETENSI_HARI" -print -delete || true
echo "[backup] retensi ${RETENSI_HARI} hari diterapkan"
