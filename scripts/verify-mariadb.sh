#!/usr/bin/env bash
# verify-mariadb.sh — cek jalur produksi (MariaDB) tanpa menyentuh schema lain.
#
#   ./scripts/verify-mariadb.sh            # baca DATABASE_URL dari .env.mariadb
#   DATABASE_URL=... ./scripts/verify-mariadb.sh
#
# Yang dilakukan (semuanya HANYA di schema app):
#   1. sambung, tampilkan daftar tabel + charset schema (read-only)
#   2. alembic upgrade head  → bikin/menyesuaikan tabel di schema itu
#   3. tampilkan daftar tabel sesudah migrasi
#   4. scripts/bootstrap.py  → seed taxonomy + hook + akun Boss (idempoten)
#
# Yang TIDAK dilakukan: ALTER/CREATE/DROP ke schema lain, reload nginx, systemd.
# Kredensial tidak pernah dicetak ke layar.

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

VENV="$DIR/.venv/bin/python"
[[ -x "$VENV" ]] || { echo "venv belum ada" >&2; exit 2; }

if [[ -f .env.mariadb ]]; then
  # muat tanpa mengeksekusi isi sebagai perintah shell
  set -a
  # shellcheck disable=SC1090
  . <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.mariadb | sed 's/\r$//')
  set +a
  echo "[verify] memakai .env.mariadb"
fi

[[ -n "${DATABASE_URL:-}" ]] || { echo "DATABASE_URL tidak ada (dan .env.mariadb tidak ditemukan)" >&2; exit 2; }
case "$DATABASE_URL" in
  mysql*) ;;
  *) echo "DATABASE_URL bukan MariaDB/MySQL — skrip ini untuk jalur produksi." >&2; exit 2;;
esac

# pecah URL: mysql+pymysql://user:pass@host:port/db?...
creds="${DATABASE_URL#*://}"; creds="${creds%%\?*}"
userpass="${creds%%@*}"; hostpart="${creds#*@}"
DBUSER="${userpass%%:*}"; DBPASS="${userpass#*:}"
hostport="${hostpart%%/*}"; DBNAME="${hostpart#*/}"
DBHOST="${hostport%%:*}"; DBPORT="${hostport##*:}"
[[ "$DBPORT" == "$DBHOST" ]] && DBPORT=3306

echo "[verify] host=$DBHOST port=$DBPORT schema=$DBNAME user=$DBUSER (sandi disembunyikan)"

q() { MYSQL_PWD="$DBPASS" mysql --default-character-set=utf8mb4 -h "$DBHOST" -P "$DBPORT" -u "$DBUSER" -N -B -e "$1" "$DBNAME"; }

echo "== 1. Keadaan awal (read-only) =="
TABEL_AWAL=$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DBNAME';")
echo "  jumlah tabel di schema $DBNAME: $TABEL_AWAL"
q "SELECT table_name, table_collation FROM information_schema.tables WHERE table_schema='$DBNAME' ORDER BY table_name LIMIT 20;" | sed 's/^/    /' || true
echo "  charset default schema:"
q "SELECT default_character_set_name, default_collation_name FROM information_schema.schemata WHERE schema_name='$DBNAME';" | sed 's/^/    /'

echo "== 2. Alembic upgrade head =="
"$DIR/.venv/bin/alembic" upgrade head 2>&1 | sed 's/^/  /'

echo "== 3. Keadaan sesudah migrasi =="
q "SELECT table_name FROM information_schema.tables WHERE table_schema='$DBNAME' ORDER BY table_name;" | sed 's/^/    /'
TABEL_AKHIR=$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DBNAME';")
echo "  jumlah tabel sekarang: $TABEL_AKHIR"

echo "== 4. Konfirmasi zona waktu sesi =="
q "SELECT @@session.time_zone, @@global.time_zone, NOW();" | sed 's/^/    /'

echo "== 5. Seed (idempoten) =="
"$VENV" scripts/bootstrap.py 2>&1 | sed 's/^/  /'

echo "== 6. Verifikasi skema lain TIDAK tersentuh =="
q "SELECT table_schema, COUNT(*) AS n FROM information_schema.tables GROUP BY table_schema ORDER BY table_schema;" | sed 's/^/    /'

echo
echo "[verify] selesai. Untuk menjalankan app di jalur ini:"
echo "  set -a; . ./.env.mariadb; set +a"
echo "  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8098"
