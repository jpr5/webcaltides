#!/bin/sh
# Mutation check: a copy of the module with T_h computed as a plain Float
# (from a Float day count) instead of exactly. The spec suite must FAIL on it.
# The mutant counts as killed only when rspec ran all 783 examples and at
# least one failed, with no error outside the examples. A load error or
# "0 examples" is an ERROR (exit 2), not a kill. Exit 1: the mutant survived.
set -eu
here=$(cd "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sed 's/th = ((d - d.floor) \* 360).to_f     # hour angle.*/df = d.to_f; th = (df - df.floor) * 360.0   # MUTANT: plain Float T_h/' \
    "$here/lib/nodal_schureman.rb" > "$tmp/nodal_schureman.rb"
if cmp -s "$here/lib/nodal_schureman.rb" "$tmp/nodal_schureman.rb"; then
    echo "mutation did not apply" >&2
    exit 2
fi
echo "--- mutant diff ---"
diff "$here/lib/nodal_schureman.rb" "$tmp/nodal_schureman.rb" || true
echo "--- rspec on mutant ---"
cd "$here"
if NODAL_LIB="$tmp/nodal_schureman.rb" bundle exec rspec -O /dev/null spec/unit/nodal_schureman_spec.rb > "$tmp/out.txt" 2>&1; then
    rc=0
else
    rc=$?
fi
grep -E 'V0\+u: \|d\|=' "$tmp/out.txt" | sed 's/^ *//' | sort -t= -k2 -g | tail -3
grep -E 'examples,' "$tmp/out.txt" || true
echo "rspec exit=$rc"
if grep -q 'error.* occurred outside of examples' "$tmp/out.txt" || ! grep -Eq '^783 examples, [0-9]+ failures?$' "$tmp/out.txt"; then
    echo "ERROR: rspec did not run the 783 examples (load error or wrong count)"
    exit 2
fi
if [ "$rc" -eq 0 ] || grep -Eq '^783 examples, 0 failures$' "$tmp/out.txt"; then
    echo "MUTANT SURVIVED"
    exit 1
fi
echo "MUTANT KILLED"
