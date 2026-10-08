#!/usr/bin/env bash
# R2c = change A engine + B3 dataset (harmonics-b2-fixes 5f5356b). Same harness calls as gate2/run_R2b.sh.
set -uo pipefail
trap 'kill 0' EXIT
T="$(cd "$(dirname "$0")/.." && pwd)"
H="${OTC_GATES_DIR:-${OTC_WORK:-$HOME/.local/share/opentideconstants/work}/gates}"
WT="${WT:?set WT to the B worktree (was harmonics-b2-fixes)}"
cd $H
export TICON_FILE=$H/builds/B3/ticon.json
date +%s
$T/run_variant.sh $WT R2c model noaa W1,W2,W3 8; echo "R2c exit=$?"
$T/run_variant.sh $WT R2c_other model @$H/sets/others.json W1,W2,W3 8; echo "R2c_other exit=$?"
$T/run_variant.sh $WT C_R2c_switch6 model @$H/sets/switch6.json W1,W2,W3 4; echo "switch6 exit=$?"
ruby $T/safety/run_safety.rb $WT R2c-5f5356b 8; echo "safety exit=$?"
date +%s
