#!/usr/bin/env bash
# smoke-api.sh — smoke test /api/v1 memakai FIXTURE ASLI dari @content.
#
#   tests/fixtures/agent-api-fixtures-v1.json
#   (asal: /root/content-workspace/gamis-jumbo/agent-api-fixtures-v1.json)
#
# Skrip ini menembak endpoint yang SEBENARNYA (bukan unit test). Jalankan setelah
# app hidup di 127.0.0.1:8099:
#
#   TOKEN=<token agent> ./scripts/smoke-api.sh
#   BASE=http://127.0.0.1:8099 DUE=2026-10-09T20:05:00+07:00 TOKEN=<token> ./scripts/smoke-api.sh
#
# Catatan penting: payload JSON dikirim dengan `curl --data-binary @file` dan TIDAK
# pernah melewati variabel bash. Body fixture memuat tanda kutip escapement; kalau
# dilewatkan shell, JSON-nya rusak dan API menjawab 400. Kesalahan ini nyata dan
# pernah terjadi pada versi pertama skrip ini — sekarang dijaga di sini.
#
# Keluar dengan kode != 0 kalau ada satu saja pemeriksaan gagal.

set -uo pipefail

BASE="${BASE:-http://127.0.0.1:8099}"
TOKEN="${TOKEN:-}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="${FIXTURE:-$DIR/tests/fixtures/agent-api-fixtures-v1.json}"
DUE="${DUE:-$(date -d '+2 day' +%Y-%m-%d 2>/dev/null || date -v+2d +%Y-%m-%d)T20:05:00+07:00}"

if [[ -z "$TOKEN" ]]; then
  echo "TOKEN belum diisi. Buat di UI /settings/tokens lalu:" >&2
  echo "  TOKEN=<token> ./scripts/smoke-api.sh" >&2
  exit 2
fi

command -v jq >/dev/null || { echo "butuh jq" >&2; exit 2; }
[[ -f "$FIXTURE" ]] || { echo "fixture tidak ada: $FIXTURE" >&2; exit 2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0; SKIP=0
declare -a GAGAL=()

ok()  { PASS=$((PASS+1)); printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); GAGAL+=("$1"); printf '  \033[31mFAIL\033[0m %s — %s\n' "$1" "${2:-}"; }
skip() { SKIP=$((SKIP+1)); printf '  \033[33mSKIP\033[0m %s\n' "$1"; }

# req METHOD PATH [FILE_BODY] [EXTRA_HEADER]
# hasil: $STATUS (kode HTTP), isi respons di $TMP/body
STATUS=""
req() {
  local metode="$1" path="$2" file="${3:-}" extra="${4:-}"
  local -a args=(-sS -o "$TMP/body" -w '%{http_code}' -X "$metode"
                 -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json')
  [[ -n "$file" ]] && args+=(-H 'Content-Type: application/json' --data-binary "@$file")
  [[ -n "$extra" ]] && args+=(-H "$extra")
  STATUS=$(curl "${args[@]}" "$BASE$path")
}

expect() { # expect <nama> <harapan> <aktual>
  if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1" "harap=$2 dapat=$3"; fi
}

# body fixture → file (JSON persis, tanpa perantara shell)
body_of() { # body_of <file> <bagian> <id>
  jq -c --arg id "$3" --arg bagian "$2" '.[$bagian][] | select(.id==$id) | .body' "$FIXTURE" > "$1"
}

echo "== 0. Kesehatan & identitas token =="
req GET /api/v1/health ;   expect "health 200 (uji #15 jalur loopback)" 200 "$STATUS"
req GET /api/v1/me ;       expect "me 200" 200 "$STATUS"
req GET /api/v1/taxonomy ; expect "taxonomy 200" 200 "$STATUS"
TAX="$(cat "$TMP/body")"

echo "== 1. Positif: FX-01..FX-06 (uji #5) =="
declare -A CID
for fid in $(jq -r '.positif[].id' "$FIXTURE"); do
  ikey=$(jq -r --arg id "$fid" '.positif[] | select(.id==$id) | .idempotency_key' "$FIXTURE")
  body_of "$TMP/fx.json" positif "$fid"
  req POST /api/v1/contents "$TMP/fx.json" "Idempotency-Key: $ikey"
  expect "$fid → 201" 201 "$STATUS"
  CID["$fid"]=$(jq -r '.data.id // empty' "$TMP/body" 2>/dev/null)
  st=$(jq -r '.data.status // empty' "$TMP/body" 2>/dev/null)
  if [[ "$st" == "review" ]]; then ok "$fid status=review"; else bad "$fid status" "dapat=$st"; fi
done

echo "== 2. Idempotency (uji #7): NX-05 & NX-06 =="
FX01_KEY=$(jq -r '.positif[0].idempotency_key' "$FIXTURE")
body_of "$TMP/fx01.json" positif FX-01
req POST /api/v1/contents "$TMP/fx01.json" "Idempotency-Key: $FX01_KEY"
expect "NX-05 replay → 200" 200 "$STATUS"
cp "$TMP/body" "$TMP/replay1"
req POST /api/v1/contents "$TMP/fx01.json" "Idempotency-Key: $FX01_KEY"
if diff -q <(jq -S . "$TMP/replay1") <(jq -S . "$TMP/body") >/dev/null; then
  ok "NX-05 body replay identik"
else
  bad "NX-05 body replay identik" "beda"
fi
body_of "$TMP/nx06.json" negatif NX-06
req POST /api/v1/contents "$TMP/nx06.json" "Idempotency-Key: $FX01_KEY"
expect "NX-06 key sama body beda → 409" 409 "$STATUS"

echo "== 3. Negatif: NX-01..NX-04 (uji #6, #16) =="
for spec in NX-01 NX-02 NX-03 NX-04; do
  harap=$(jq -r --arg id "$spec" '.negatif[] | select(.id==$id) | .expected_http' "$FIXTURE")
  body_of "$TMP/nx.json" negatif "$spec"
  req POST /api/v1/contents "$TMP/nx.json" "Idempotency-Key: smoke-$spec-$$"
  expect "$spec → $harap" "$harap" "$STATUS"
done

echo "== 4. Warning pemicu (iii): FX-05 lalu FX-04 (uji #20) =="
body_of "$TMP/fx05.json" positif FX-05
body_of "$TMP/fx04.json" positif FX-04
req POST /api/v1/contents "$TMP/fx05.json" "Idempotency-Key: smoke-fx05-b-$$"
req POST /api/v1/contents "$TMP/fx04.json" "Idempotency-Key: smoke-fx04-b-$$"
expect "FX-04 tetap 201 (nol blokir)" 201 "$STATUS"
n=$(jq '[.data.warning[]? | select(.kode=="usulan_jadwal_tabrakan")] | length' "$TMP/body" 2>/dev/null)
if [[ "${n:-0}" -ge 1 ]]; then
  ok "FX-04 memuat warning usulan_jadwal_tabrakan"
else
  bad "FX-04 warning tabrakan" "dapat=$(jq -c '.data.warning?' "$TMP/body" 2>/dev/null)"
fi

echo "== 5. Queue & reels (uji #17) =="
CID_REELS="${CID[FX-05]:-}"
req GET "/api/v1/queue?due=$DUE"
expect "queue?due=$DUE → 200" 200 "$STATUS"

# D20 hanya bisa diuji penuh kalau item reels memang sudah `terjadwal` — dan itu
# keputusan Boss (approve + slot) yang tidak punya jalur API. Kalau belum, uji ini
# dilaporkan SKIP, bukan lulus palsu.
STATUS_REELS=""
if [[ -n "$CID_REELS" ]]; then
  req GET "/api/v1/contents/$CID_REELS"
  STATUS_REELS=$(jq -r '.data.status // empty' "$TMP/body" 2>/dev/null)
fi
if [[ -z "$CID_REELS" ]]; then
  skip "uji reels dilewati (item FX-05 tidak terbentuk)"
elif [[ "$STATUS_REELS" != "terjadwal" ]]; then
  skip "uji reels dilewati — status item FX-05 sekarang '$STATUS_REELS' (butuh approve+jadwal Boss lewat UI, lalu jalankan ulang skrip ini)"
else
  req GET "/api/v1/queue?due=$DUE"
  hadir=$(jq --argjson id "$CID_REELS" '[.data[] | select(.id==$id)] | length' "$TMP/body" 2>/dev/null)
  if [[ "$hadir" == "0" ]]; then ok "reels tidak masuk queue?due= (D20)"; else bad "reels masuk queue" "id=$CID_REELS"; fi
  man=$(jq --argjson id "$CID_REELS" '[.butuh_boss.data[] | select(.id==$id)] | length' "$TMP/body" 2>/dev/null)
  if [[ "${man:-0}" -ge 1 ]]; then ok "reels muncul di butuh_boss"; else bad "reels di butuh_boss" "id=$CID_REELS"; fi
  req GET "/api/v1/queue?due=$DUE&include_manual=1"
  hadir=$(jq --argjson id "$CID_REELS" '[.data[] | select(.id==$id)] | length' "$TMP/body" 2>/dev/null)
  if [[ "$hadir" == "1" ]]; then ok "include_manual=1 menampilkan reels"; else bad "include_manual=1" "id=$CID_REELS"; fi
fi

echo "== 6. Kuota tiga angka (uji #19) =="
if echo "$TAX" | jq -e '.pilar[] | .kuota_terpakai | has("terpakai_minggu_ini") and has("pipeline_aktif") and has("belum_dijadwalkan")' >/dev/null; then
  ok "taxonomy membalas 3 angka kuota per pilar"
else
  bad "taxonomy 3 angka" "$TAX"
fi

echo "== 7. TZ (uji #9) =="
if echo "$TAX" | jq -e '.tz == "Asia/Jakarta"' >/dev/null; then
  ok "tz=Asia/Jakarta"
else
  bad "tz" "$(echo "$TAX" | jq -r .tz)"
fi
req GET "/api/v1/queue?due=$DUE"
if jq -e '.due | endswith("+07:00")' "$TMP/body" >/dev/null 2>&1; then
  ok "due memakai offset +07:00"
else
  bad "offset due" "$(jq -r .due "$TMP/body" 2>/dev/null)"
fi

echo "== 8. Body bukan JSON → 400 (bukan 500) =="
S=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/contents" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data-binary '{bukan-json}')
expect "JSON rusak → 400" 400 "$S"

echo "== 9. Auth negatif (uji #6) =="
S=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/v1/taxonomy")
expect "tanpa token → 401" 401 "$S"
S=$(curl -sS -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer tidak-valid' "$BASE/api/v1/taxonomy")
expect "token ngawur → 401" 401 "$S"

echo
echo "---------------------------------------------"
echo "LULUS=$PASS  GAGAL=$FAIL  DILEWATI=$SKIP"
echo "(uji D20 penuh butuh approve+jadwal Boss lewat UI; versi otomatisnya ada di pytest)"
if [[ "$FAIL" -gt 0 ]]; then
  printf 'Gagal: %s\n' "${GAGAL[@]}"
  exit 1
fi
echo "Semua pemeriksaan lolos."
