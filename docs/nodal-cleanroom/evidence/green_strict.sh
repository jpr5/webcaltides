#!/bin/sh
# Strict run: the current spec with the (V0+u) tolerance set to 1e-9 instead of 4e-9.
# Exits 0 only when rspec ran every example and exactly the expected number
# failed (783 examples, 3 failures), with no error outside the examples.
set -u
cd "$(dirname "$0")/../../.."
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/spec/unit"; ln -s "$PWD/spec/fixtures" "$tmp/spec/fixtures"
sed 's/^    tolerance_v0u = 4e-9$/    tolerance_v0u = 1e-9/' spec/unit/nodal_schureman_spec.rb > "$tmp/spec/unit/nodal_schureman_spec.rb"
grep -q 'tolerance_v0u = 1e-9' "$tmp/spec/unit/nodal_schureman_spec.rb" || { echo "sed did not apply"; exit 2; }
NODAL_LIB="$PWD/lib/nodal_schureman.rb" bundle exec rspec -O /dev/null "$tmp/spec/unit/nodal_schureman_spec.rb" > "$tmp/o" 2>&1; rc=$?
grep -E 'V0\+u: \|d\|=|examples,' "$tmp/o" | sed 's/^ *//'
grep -E '^rspec ' "$tmp/o" | sed 's/^rspec [^#]*# //'
echo "rspec exit=$rc"
if grep -q 'error.* occurred outside of examples' "$tmp/o" || ! grep -Eq '^783 examples, 3 failures$' "$tmp/o"; then
    echo "ERROR: expected exactly '783 examples, 3 failures' and no load error"
    exit 1
fi
