#!/usr/bin/env bash
# Usage: tools/run_variant.sh <worktree> <run> [mode=model|rn] [srcs=noaa|all|csv] [windows=W1,W2,W3] [nprocs=8]
# Env: TICON_FILE (default <worktree>/data/ticon.json, resolved), KEEP_CACHE=1 to keep cache/<run>.
set -euo pipefail
H="$(cd "$(dirname "$0")/.." && pwd)"
WT="$(cd "$1" && pwd)"; RUN="$2"; MODE="${3:-model}"; SRCS="${4:-noaa}"; WINS="${5:-W1,W2,W3}"; NP="${6:-8}"
TF="${TICON_FILE:-$WT/data/ticon.json}"; TF="$(cd "$(dirname "$TF")" && pwd)/$(basename "$TF")"
[ -L "$TF" ] && TF="$(cd "$(dirname "$TF")" && cd "$(dirname "$(readlink "$TF")")" && pwd)/$(basename "$(readlink "$TF")")"
[ -f "$TF" ] || { echo "TICON_FILE missing: $TF" >&2; exit 2; }
SHA="$(git -C "$WT" rev-parse HEAD)"
LOG="$H/logs/$RUN"; mkdir -p "$LOG"
[ "${KEEP_CACHE:-0}" = 1 ] || rm -rf "$H/cache/$RUN"
IFS=, read -ra WL <<< "$WINS"
for w in "${WL[@]}"; do rm -rf "$H/results/$RUN/$w"; done
echo "run=$RUN sha=$SHA mode=$MODE srcs=$SRCS wins=$WINS np=$NP ticon=$TF" | tee "$LOG/run.txt"
T0=$(date +%s)
pids=()
for ((i = 0; i < NP; i++)); do
    (cd "$WT" && TICON_FILE="$TF" bundle exec ruby "$H/tools/runner.rb" "$RUN" "$i" "$NP" "$MODE" "$SRCS" "$WINS" > "$LOG/shard_$i.log" 2>&1) &
    pids+=($!)
done
fail=0
for p in "${pids[@]}"; do wait "$p" || fail=1; done
T1=$(date +%s)
[ "$fail" = 0 ] || { echo "shard failure; see $LOG/shard_*.log" >&2; exit 1; }
ruby "$H/tools/merge.rb" "$RUN" "$WINS"
ruby "$H/tools/manifest.rb" "$RUN" "$WT" "$SHA" "$TF" "$MODE" "$SRCS" "$WINS" "$NP" "$((T1 - T0))"
echo "run=$RUN wall=$((T1 - T0))s" | tee -a "$LOG/run.txt"
