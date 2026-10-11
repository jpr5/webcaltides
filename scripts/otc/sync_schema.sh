#!/usr/bin/env bash
# Copy the OTC format 0.5 schema and its example document from the site repo
# (opentideconstants/opentideconstants) at a pinned commit, and check SHA-256.
#
#   scripts/otc/sync_schema.sh          copy, then check
#   scripts/otc/sync_schema.sh --check  only check the copies in this repo
#
# The site repo is read from $OTC_SITE_REPO (default /proj/opentideconstants)
# with `git show`. If that checkout does not have the pinned commit, the files
# come from raw.githubusercontent.com at the same commit.
#
# A downloaded file replaces the copy in this repo only if its SHA-256 matches
# the pin, and it keeps the mode of that copy. Both files are always checked;
# the exit status is non-zero if either fails.
#
# To move to a new schema commit: change SITE_COMMIT and both SHA-256 values in
# the same commit as any writer change the new schema needs.
set -euo pipefail

SITE_COMMIT=b4ff8d1ffa6701d209eec19fc25f9f02aea99c5f   # merge of PR #13 (schema 0.5: mean current, below_chart_datum)
SCHEMA_SRC=src/schema/otc-0.5.schema.json
SCHEMA_SHA256=efc42311ee8f3484bb0c66b1985fdf1d55411cc2df7517f28de89488176009e9
EXAMPLE_SRC=src/schema/example.json
EXAMPLE_SHA256=9842998a497e88d94870ee934dc61e7df6e7fdf07ffd8a4497ba9a963e054ce7

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
schema_dst="$here/schema/otc-0.5.schema.json"
example_dst="$here/tests/fixtures/example-0.5.json"
site_repo="${OTC_SITE_REPO:-/proj/opentideconstants}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }

check() { # <file> <expected sha256>
    local got
    got="$(sha256 "$1")" || return 1
    if [[ "$got" != "$2" ]]; then
        echo "FAIL: $1 has SHA-256 $got, expected $2 (site commit $SITE_COMMIT)" >&2
        return 1
    fi
    echo "ok: $1 matches site commit ${SITE_COMMIT:0:7}"
}

fetch() { # <path in site repo> <destination> <expected sha256>
    local tmp="$tmpdir/$(basename "$2")"
    if git -C "$site_repo" cat-file -e "$SITE_COMMIT:$1" 2>/dev/null; then
        git -C "$site_repo" show "$SITE_COMMIT:$1" > "$tmp" || return 1
    else
        curl -fsSL "https://raw.githubusercontent.com/opentideconstants/opentideconstants/$SITE_COMMIT/$1" \
            -o "$tmp" || return 1
    fi
    if ! check "$tmp" "$3" >/dev/null 2>&1; then
        echo "FAIL: $1 at ${SITE_COMMIT:0:7} has SHA-256 $(sha256 "$tmp"), expected $3;" \
             "$2 is not changed" >&2
        return 1
    fi
    mkdir -p "$(dirname "$2")" || return 1
    cp "$tmp" "$2" || return 1   # cp onto an existing file keeps its mode
}

status=0
if [[ "${1:-}" != "--check" ]]; then
    fetch "$SCHEMA_SRC" "$schema_dst" "$SCHEMA_SHA256" || status=1
    fetch "$EXAMPLE_SRC" "$example_dst" "$EXAMPLE_SHA256" || status=1
fi
check "$schema_dst" "$SCHEMA_SHA256" || status=1
check "$example_dst" "$EXAMPLE_SHA256" || status=1
exit "$status"
