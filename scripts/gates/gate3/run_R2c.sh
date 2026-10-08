#!/usr/bin/env bash
# R2c = change A engine + B3 dataset (harmonics-b2-fixes 5f5356b). Same harness calls as gate2/run_R2b.sh.
set -uo pipefail
trap 'kill 0' EXIT
H=/Users/jpr5/.local/share/copilotkit/cr/webcaltides-harmonics-eval
WT=/Users/jpr5/.local/state/worktrees/webcaltides/harmonics-b2-fixes
cd $H
export TICON_FILE=$H/builds/B3/ticon.json
date +%s
tools/run_variant.sh $WT R2c model noaa W1,W2,W3 8; echo "R2c exit=$?"
tools/run_variant.sh $WT R2c_other model @$H/sets/others.json W1,W2,W3 8; echo "R2c_other exit=$?"
tools/run_variant.sh $WT C_R2c_switch6 model @/Users/jpr5/.local/share/copilotkit/cr/webcaltides-harmonics-c/impl/switch6.json W1,W2,W3 4; echo "switch6 exit=$?"
ruby tools/safety/run_safety.rb $WT R2c-5f5356b 8; echo "safety exit=$?"
date +%s
