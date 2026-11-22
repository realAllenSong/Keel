#!/bin/sh
# Rebuild backend/pf/market/fx_baseline.csv from a ledger whose daily job has downloaded FRED
# rates (public market data only: first trading day of each month, 1 CNY in USD).
set -eu
DB="${1:-$HOME/Library/Application Support/Keel/data/ledger.db}"
OUT="$(dirname "$0")/../backend/pf/market/fx_baseline.csv"
{
  echo "# 1 CNY in USD, first trading day of each month (FRED DEXCHUS, inverted). Public market data."
  echo "# Fallback only: rates the daily job downloads take precedence. Refresh with tools/refresh_fx_baseline.sh."
  echo "date,base,quote,rate"
  sqlite3 -readonly -csv "$DB" "select min(date),'CNY','USD',rate from prices where base='CNY' and quote='USD' and source='fred' group by substr(date,1,7) order by 1"
} > "$OUT"
wc -l "$OUT"
