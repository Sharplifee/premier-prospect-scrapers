#!/usr/bin/env bash
# Nov 21 2026: the one recompute every workflow runs (convergence, buyers, app cache, map, KPI).
# Two recomputes overlapping (enrichment + a county re-read) failed with 55P03 lock_not_available, because the REST role
# has an 8s lock_timeout. Now:
#   1. wait for the recompute lease (pp_recompute_lease) — only one caller recomputes at a time, across all workflows
#      and the scraper run; a crashed holder's lease expires after 15 minutes;
#   2. retry each step on a lock or timeout error (55P03, 57014, HTTP 5xx) up to 6 times, 30s apart;
#   3. release the lease on exit, even on failure.
set -uo pipefail
: "${SUPABASE_URL:?}" "${SUPABASE_SERVICE_KEY:?}"
HOLDER="${GITHUB_WORKFLOW:-local}#${GITHUB_RUN_ID:-$$}"
H=(-H "apikey: $SUPABASE_SERVICE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_KEY" -H "Content-Type: application/json")
FNS="${PP_RECOMPUTE_FNS:-pp_compute_convergence pp_compute_buyers pp_refresh_app_cache pp_refresh_map_and_perf_bt pp_refresh_kpi_cache}"
rpc() { curl -s -o /tmp/pp_rpc.txt -w "%{http_code}" -X POST "${H[@]}" "$SUPABASE_URL/rest/v1/rpc/$1" -d "$2" --max-time 180; }

waited=0
until [ "$(rpc pp_recompute_lease_acquire "{\"p_holder\":\"$HOLDER\",\"p_ttl_seconds\":900}"; cat /tmp/pp_rpc.txt)" = "200true" ]; do
  [ "$waited" -ge 2400 ] && { echo "::error::recompute lease still held after 40 min: $(rpc pp_recompute_lease_status '{}' >/dev/null; cat /tmp/pp_rpc.txt)"; exit 1; }
  [ $((waited % 120)) -eq 0 ] && echo "waiting for recompute lease (held by: $(rpc pp_recompute_lease_status '{}' >/dev/null; head -c 160 /tmp/pp_rpc.txt))"
  sleep 20; waited=$((waited + 20))
done
echo "recompute lease acquired by $HOLDER after ${waited}s"
trap 'rpc pp_recompute_lease_release "{\"p_holder\":\"$HOLDER\"}" >/dev/null; echo "recompute lease released"' EXIT

for fn in $FNS; do
  for attempt in 1 2 3 4 5 6; do
    code=$(rpc "$fn" '{}')
    echo "$fn -> $code $(head -c 40 /tmp/pp_rpc.txt)"
    [ "$code" -lt 400 ] && break
    if [ "$attempt" -lt 6 ] && { [ "$code" -ge 500 ] || grep -q -E '55P03|57014' /tmp/pp_rpc.txt; }; then
      echo "  $fn: lock or timeout (attempt $attempt) — retrying in 30s"; sleep 30; continue
    fi
    echo "::error::$fn failed: $(head -c 300 /tmp/pp_rpc.txt)"; exit 1
  done
  # keep the lease alive through long runs
  rpc pp_recompute_lease_acquire "{\"p_holder\":\"$HOLDER\",\"p_ttl_seconds\":900}" >/dev/null
done
