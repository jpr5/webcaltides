#!/usr/bin/env bash
# Copy the OTC format 1.0 schema and its example document from the site repo
# (opentideconstants/opentideconstants) at a pinned commit, and check SHA-256.
#
#   scripts/otc/sync_schema.sh --site-repo DIR  copy from a site checkout, then check
#   scripts/otc/sync_schema.sh --github         copy from raw.githubusercontent.com, then check
#   scripts/otc/sync_schema.sh --check          only check the copies in this repo
#
# The source is always named on the command line; there is no default checkout.
# --site-repo reads the pinned commit with `git show` and fails if the checkout
# does not have it; --github downloads the same commit.
#
# A downloaded file replaces the copy in this repo only if its SHA-256 matches
# the pin, and it keeps the mode of that copy. Both files are always checked;
# the exit status is non-zero if either fails.
#
# To move to a new schema commit: change SITE_COMMIT and both SHA-256 values in
# the same commit as any writer change the new schema needs. release_writer.py pins the
# same schema SHA-256 (SCHEMA_SHA256) and refuses a copy that does not match it.
set -euo pipefail

SITE_COMMIT=533eafa0ad1e5a79f9c3da6cd024f200b131fc03   # approved explicit restricted TICON policy binding
SCHEMA_SRC=src/schema/otc-1.0.schema.json
SCHEMA_SHA256=c304322a7efc4546dcf720bb81710fc0dfec6cd9eaa63dfeb184e377508046b9
EXAMPLE_SRC=src/schema/example.json
EXAMPLE_SHA256=6fb0eecbd80ddce8c7ff9ecdc67da9ee1d89ff33979271b3de45e6c4ae99205a

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
schema_dst="$here/schema/otc-1.0.schema.json"
example_dst="$here/tests/fixtures/example-1.0.json"
mode="" site_repo=""
case "${1:-}" in
    --check) mode=check ;;
    --github) mode=github ;;
    --site-repo)
        [[ -n "${2:-}" ]] || { echo "usage: $0 --site-repo DIR | --github | --check" >&2; exit 2; }
        mode=repo site_repo="$2" ;;
    *) echo "usage: $0 --site-repo DIR | --github | --check" >&2; exit 2 ;;
esac

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
    if [[ "$mode" == repo ]]; then
        if ! git -C "$site_repo" cat-file -e "$SITE_COMMIT:$1" 2>/dev/null; then
            echo "FAIL: $site_repo has no $SITE_COMMIT:$1" >&2
            return 1
        fi
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
if [[ "$mode" != check ]]; then
    fetch "$SCHEMA_SRC" "$schema_dst" "$SCHEMA_SHA256" || status=1
    fetch "$EXAMPLE_SRC" "$example_dst" "$EXAMPLE_SHA256" || status=1
fi
check "$schema_dst" "$SCHEMA_SHA256" || status=1
check "$example_dst" "$EXAMPLE_SHA256" || status=1
exit "$status"
