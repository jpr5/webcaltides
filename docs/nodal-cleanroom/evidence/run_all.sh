#!/bin/sh
# Regenerates every evidence file. RED uses the stub from commit 4cffaff (committed here as evidence/stub_nodal_schureman.rb);
# the strict run uses a copy of the spec with a 1e-9 V0+u tolerance. Each file starts with the
# command that produced it and the SHA-256 of the module, the spec and the oracle.
#
# The files are written to a temporary directory first. They replace the
# committed evidence only when every script exits 0 and green.txt shows
# "783 examples, 0 failures"; otherwise this script exits 1 and changes nothing.
# Each copy into place is checked; if one fails, the script exits 1 and does
# not report success (files copied before it are then already replaced).
# The sub-scripts check their own expected counts (red 783/781, strict 783/3,
# mutant at least one failure).
set -u
cd "$(dirname "$0")/../../.."
command -v shasum >/dev/null 2>&1 || { echo "ERROR: shasum not found" >&2; exit 2; }
sha() {
    h=$(shasum -a 256 "$1" | cut -c1-64)
    [ ${#h} -eq 64 ] || { echo "ERROR: no SHA-256 for $1" >&2; exit 2; }
    echo "$h"
}
h_lib=$(sha lib/nodal_schureman.rb) || exit 2
h_spec=$(sha spec/unit/nodal_schureman_spec.rb) || exit 2
h_oracle=$(sha spec/fixtures/harmonics/nodal_oracle.json) || exit 2
hashes="sha256 lib/nodal_schureman.rb=$h_lib spec/unit/nodal_schureman_spec.rb=$h_spec spec/fixtures/harmonics/nodal_oracle.json=$h_oracle"
out_dir=$(mktemp -d); trap 'rm -rf "$out_dir"' EXIT
failed=0
run() {
    out=$1; shift
    { echo "\$ $*"; echo "# cwd: repo root; $hashes"; "$@" 2>&1; echo "script exit=$?"; } > "$out_dir/$out"
    if ! grep -q '^script exit=0$' "$out_dir/$out"; then
        echo "FAILED: $out ($(tail -1 "$out_dir/$out"))" >&2
        failed=1
    fi
}
run green.txt bundle exec rspec -O /dev/null spec/unit/nodal_schureman_spec.rb
grep -Eq '^783 examples, 0 failures$' "$out_dir/green.txt" || { echo "FAILED: green.txt is not '783 examples, 0 failures'" >&2; failed=1; }
run worst_errors.txt ruby docs/nodal-cleanroom/evidence/worst_errors.rb
run v0_variants.txt ruby docs/nodal-cleanroom/evidence/v0_variants.rb
run mutation_th_float.txt sh docs/nodal-cleanroom/evidence/mutation_th_float.sh
run red.txt sh docs/nodal-cleanroom/evidence/red_stub.sh
run green-strict-1e-9.txt sh docs/nodal-cleanroom/evidence/green_strict.sh
if [ "$failed" -ne 0 ]; then
    echo "run_all.sh: evidence NOT updated" >&2
    exit 1
fi
copy_failed=0
for f in "$out_dir"/*.txt; do
    dest="docs/nodal-cleanroom/evidence/$(basename "$f")"
    if ! cp "$f" "$dest" || ! cmp -s "$f" "$dest"; then
        echo "FAILED: could not write $dest" >&2
        copy_failed=1
    fi
done
if [ "$copy_failed" -ne 0 ]; then
    echo "run_all.sh: evidence NOT fully updated (a copy failed; check git status)" >&2
    exit 1
fi
echo "run_all.sh: evidence updated"
