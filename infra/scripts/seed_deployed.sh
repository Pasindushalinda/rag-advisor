#!/usr/bin/env bash
# Seeds the DEPLOYED stack: Langfuse prompts the graphs fetch at runtime, then kicks off filing
# ingestion into the deployed Qdrant through the API's /ingest endpoint.
# Values come from Terraform outputs at run time, so no secret is typed, stored or printed.
# Usage: infra/scripts/seed_deployed.sh [prompts|ingest|all] [TICKER...]   (default: all)
# Tickers limit `ingest`; re-running a ticker adds its chunks again, so only repeat ones that failed.
set -euo pipefail

INFRA="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$INFRA/../backend"
STEP="${1:-all}"
shift || true
ONLY=("$@")

tf() { terraform -chdir="$INFRA" output -raw "$1"; }

seed_prompts() {
  # The Langfuse SDK reads these from the environment. They are exported, not passed as arguments,
  # and an already-set variable wins over backend/.env, so the local stack is never targeted.
  export LANGFUSE_HOST="$(tf langfuse_url)" LANGFUSE_BASE_URL="$(tf langfuse_url)"
  export LANGFUSE_PUBLIC_KEY="$(tf langfuse_public_key)"
  export LANGFUSE_SECRET_KEY="$(tf langfuse_secret_key)"
  # .venv python, not `uv run`: uv would try to build the backend as a package (it has no src layout).
  # Only the prompts the deployed graph fetches; the other seed_data scripts are for experiments.
  (cd "$BACKEND" && .venv/bin/python seed_data/create_retriever_prompts.py \
    && .venv/bin/python seed_data/create_investor_advisor_prompts.py)
}

ingest() {
  local api; api="$(tf api_url)"
  # Header read from a file descriptor so the admin key never appears in `ps` output.
  local filings=(
    'AAPL|Apple Inc|10-K|https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm'
    'MSFT|Microsoft Corporation|10-K|https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm'
    'NFLX|Netflix Inc|10-K|https://www.sec.gov/Archives/edgar/data/1065280/000106528026000034/nflx-20251231.htm'
    'GOOG|Alphabet Inc|10-K|https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm'
  )
  local row ticker company form url
  for row in "${filings[@]}"; do
    IFS='|' read -r ticker company form url <<<"$row"
    if [ "${#ONLY[@]}" -gt 0 ] && [[ ! " ${ONLY[*]} " == *" $ticker "* ]]; then continue; fi
    printf '%s: ' "$ticker"
    curl -sS -m 60 -X POST "$api/ingest" \
      -H @<(printf 'Authorization: Bearer %s\n' "$(tf admin_api_key)") \
      -H 'Content-Type: application/json' \
      -d "{\"ticker\":\"$ticker\",\"company\":\"$company\",\"form_type\":\"$form\",\"url\":\"$url\"}"
    echo
  done
}

case "$STEP" in
  prompts) seed_prompts ;;
  ingest)  ingest ;;
  all)     seed_prompts; ingest ;;
  *) echo "usage: $0 [prompts|ingest|all]" >&2; exit 2 ;;
esac
