#!/bin/sh
# RED: run the current spec against the empty stub from commit 4cffaff
# (committed here as docs/nodal-cleanroom/evidence/stub_nodal_schureman.rb).
# Exits 0 only when rspec ran every example and exactly the expected number
# failed (783 examples, 781 failures), with no error outside the examples.
set -u
cd "$(dirname "$0")/../../.."
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cp docs/nodal-cleanroom/evidence/stub_nodal_schureman.rb "$tmp/nodal_schureman.rb"
NODAL_LIB="$tmp/nodal_schureman.rb" bundle exec rspec -O /dev/null spec/unit/nodal_schureman_spec.rb > "$tmp/o" 2>&1; rc=$?
grep -E '^rspec' "$tmp/o" | head -3
echo ...
grep -E 'examples,' "$tmp/o"; echo "rspec exit=$rc"
if grep -q 'error.* occurred outside of examples' "$tmp/o" || ! grep -Eq '^783 examples, 781 failures$' "$tmp/o"; then
    echo "ERROR: expected exactly '783 examples, 781 failures' and no load error"
    exit 1
fi
