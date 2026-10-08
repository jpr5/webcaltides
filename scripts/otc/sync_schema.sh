#!/usr/bin/env bash
# Copy the OTC format 0.2 schema and its example document from the site repo
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

SITE_COMMIT=88b6418edeeda065ba3e96ee26368faefcbd7f12   # merge of PR #4 (schema 0.2, K0)
SCHEMA_SRC=src/schema/otc-0.2.schema.json
SCHEMA_SHA256=5645522d5cbc34d21918f274f1b22daae3e3622459fe74ec90c2b7b30f4d6894
EXAMPLE_SRC=src/schema/example.json
EXAMPLE_SHA256=3074c8003d21717d6624b911a51a4d1e3548ce373dc41fe1ace37711a2182462

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
schema_dst="$here/schema/otc-0.2.schema.json"
example_dst="$here/tests/fixtures/example-0.2.json"
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
