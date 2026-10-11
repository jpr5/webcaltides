#!/usr/bin/env bash
# Run congen (XTide, David Flater; GPLv3) as a TOOL to make the OTC TCD and the values of the
# release's astro tables (KP0b; prediction spec §3.4 rule 1), and dump them next to the TCD they
# must equal.
#
#   scripts/otc/tools/congen_tables.sh OUT_DIR
#
# Nothing from congen or libtcd is in this repo. The script downloads the two pinned tarballs
# into OUT_DIR/src (SHA-256 checked before anything is unpacked), builds them out of tree in
# OUT_DIR/build, and runs congen on the congen_input.txt that ships in its own tarball followed by
# the OTC definitions in otc_constituents.txt (next to this script; MA2 and MB2):
#
#   congen -b 1700 -e 2100 -tcd OUT_DIR/congen-1700-2100.tcd < congen_input+otc.txt
#
# congen writes the TCD through libtcd (public domain), the same encoding the XTide data file
# has, so its values are rounded exactly as the TCD's are (V0+u to 0.01 degree, f to 0.0001).
# congen's text output rounds f with printf instead, and differs from the TCD by one step in
# 10 of the 70,576 (constituent, year) values (measured 2026-10-10; M3 2028 and EPS2 2100 are
# GESLA constituents), so the text output is not used.
#
# The OTC TCD (FIX-CONST option A, owner decision 2026-10-11) is data/harmonics-dwf-20251228-free.tcd
# with MA2 and MB2 added: tools/tcd/tcd_merge.c (against the run's libtcd) copies every constituent
# and station of that file unchanged and appends the two constituents with the congen run's
# values (amplitude 0 at every station). Its [LAST MODIFIED] is fixed at OTC_TCD_STAMP, so the file
# is the same bytes on every run; it must hash to OTC_TCD_SHA256. XTide 2.16 reads it (FIX-CONST
# proof). The tcd gem (the Ruby engine, dump_tcd.rb) misreads it: 178 constituents is not a multiple
# of 8 (dump_tcd.rb check_gem_alignment), so the dumps here are made with libtcd (tools/tcd/tcd_dump.c
# and tcd_json.py, which write dump_tcd.rb's file byte for byte; a test checks that):
#   OUT_DIR/congen-1700-2100.json   the congen run (the values the release publishes)
#   OUT_DIR/tcd-1700-2100.json      the OTC TCD (what they must equal)
#   OUT_DIR/tcd-1900-2100.json      the OTC TCD for the GESLA fit (gesla.fit_run TCD_TABLE)
# and OUT_DIR/PROVENANCE.json records the tarballs, the command, the OTC definitions and every
# output's SHA-256. otc_pipeline.astro_tables.load_congen_run reads the dumps and checks them against it.
#
# OUT_DIR must not be inside a git work tree that would track it (the GPL tarballs and build trees
# would then show up for `git add`): the script refuses one unless that work tree ignores it. The
# usual OUT_DIR is $OTC_WORK/astro/otc-tcd-20251228-ma2mb2.
set -euo pipefail

CONGEN_URL=https://flaterco.com/files/xtide/congen-1.7-r2.tar.xz
CONGEN_SHA256=1dbae015e338e5780a505ebf233058f5e91628319a11bfbc465cb556cfb4b0d7
LIBTCD_URL=https://flaterco.com/files/xtide/libtcd-2.2.7-r3.tar.xz
LIBTCD_SHA256=e1dde9aafb771eab57c676a99b4b79d61c6800990a0e72782bc20057a8a2d877
SOURCE_TCD_SHA256=33f97cb73ac25a0fec273950c0b85ff55ddbe7edf4448e80ea8d5b3d59b0aeb1
OTC_TCD_ID=otc-tcd-20251228-ma2mb2
OTC_TCD_SHA256=ab8fa42c6dfce040924b3b035c1a87bef1cc4f10ed17449ae3ea16d0c76516a9
OTC_TCD_STAMP="2026-10-11 00:00"
OTC_ADDED=(MA2 MB2)
FIT_FIRST_YEAR=1900
FIRST_YEAR=1700
LAST_YEAR=2100

[[ $# -eq 1 ]] || { echo "usage: $0 OUT_DIR" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
tcd="$repo/data/harmonics-dwf-20251228-free.tcd"
made=()   # the directories this run creates for OUT_DIR, deepest first (removed again on a refusal)
d="$1"
while [[ ! -e "$d" ]]; do made+=("$d"); d="$(dirname "$d")"; done
mkdir -p "$1"
out="$(cd "$1" && pwd)"
if git -C "$out" rev-parse --is-inside-work-tree >/dev/null 2>&1 && ! git -C "$out" check-ignore -q .; then
    top="$(git -C "$out" rev-parse --show-toplevel)"
    for d in ${made[@]+"${made[@]}"}; do rmdir "$d"; done
    echo "$out is inside the git work tree $top, which does not ignore it: congen and libtcd must not" \
         "be vendored. Use a directory outside it, e.g. \$OTC_WORK/astro/otc-tcd-20251228-ma2mb2." >&2
    exit 2
fi
src="$out/src" build="$out/build" prefix="$out/build/prefix"
mkdir -p "$src" "$build"

if command -v shasum >/dev/null 2>&1; then
    sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }
elif command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$1" | cut -d' ' -f1; }
else
    echo "neither shasum nor sha256sum is installed" >&2
    exit 1
fi

fetch() {   # URL SHA256 -> the checked tarball in $src
    local url="$1" want="$2" f="$src/${1##*/}"
    if [[ ! -f "$f" ]]; then
        curl -sSfL --retry 3 -o "$f.part" "$url"
        mv "$f.part" "$f"
    fi
    local got
    got="$(sha256 "$f")"
    if [[ "$got" != "$want" ]]; then
        mv "$f" "$f.bad-$got"
        echo "$f: SHA-256 $got, expected $want. Moved it to $f.bad-$got; run again to download it" \
             "again (and look at the .bad file if it fails twice)." >&2
        exit 1
    fi
}

got="$(sha256 "$tcd")"
[[ "$got" == "$SOURCE_TCD_SHA256" ]] || { echo "$tcd: SHA-256 $got, expected $SOURCE_TCD_SHA256" >&2; exit 1; }
defs="$here/otc_constituents.txt"
fetch "$CONGEN_URL" "$CONGEN_SHA256"
fetch "$LIBTCD_URL" "$LIBTCD_SHA256"
tar -xJf "$src/congen-1.7-r2.tar.xz" -C "$src"
tar -xJf "$src/libtcd-2.2.7-r3.tar.xz" -C "$src"

# congen's Makefile mangles its UTF-8 sources with `sed --file`, which BSD sed lacks; -f is
# the same option in both. GNU sed is preferred when present (the script uses \| addresses).
# The mangling needs a UTF-8 locale (its patterns are UTF-8), and only the congen make step uses it.
sed_bin="$(command -v gsed || command -v sed)"
utf8="" locales="$(locale -a 2>/dev/null || true)"   # read once: grep -q in a pipe would SIGPIPE locale (pipefail)
for loc in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8; do
    if grep -qx "$loc" <<<"$locales"; then utf8="$loc"; break; fi
done
[[ -n "$utf8" ]] || { echo "no UTF-8 locale (C.UTF-8 or en_US.UTF-8) for congen's source mangling" >&2; exit 1; }
(cd "$build" && mkdir -p libtcd && cd libtcd &&
    "$src/libtcd-2.2.7/configure" --prefix="$prefix" --disable-shared >configure.log 2>&1 &&
    make >make.log 2>&1 && make install >install.log 2>&1) ||
    { echo "libtcd build failed: see $build/libtcd/*.log" >&2; exit 1; }
# On a case-insensitive file system the generated header Congen and the program congen are one
# file: make writes the header, compiles, then links the program over it. Build once, from clean.
rm -rf "$build/congen"
(cd "$build" && mkdir -p congen && cd congen &&
    "$src/congen-1.7/configure" CPPFLAGS="-I$prefix/include" LDFLAGS="-L$prefix/lib" --disable-shared \
        >configure.log 2>&1 &&
    LC_ALL="$utf8" make MANGLE="$sed_bin -f $src/congen-1.7/utf8-src/mangle.sed" >make.log 2>&1) ||
    { echo "congen build failed: see $build/congen/*.log" >&2; exit 1; }
congen="$build/congen/congen"

run_tcd="$out/congen-$FIRST_YEAR-$LAST_YEAR.tcd"
run_input="$out/congen_input+otc.txt"
cat "$src/congen-1.7/congen_input.txt" "$defs" >"$run_input"
cp "$defs" "$out/otc_constituents.txt"
rm -f "$run_tcd"
"$congen" -b "$FIRST_YEAR" -e "$LAST_YEAR" -tcd "$run_tcd" <"$run_input"
[[ -s "$run_tcd" ]] || { echo "congen wrote no TCD (built without libtcd?)" >&2; exit 1; }

# the libtcd tools (our code, tools/tcd), against the run's libtcd
for t in tcd_dump tcd_merge; do
    "${CC:-cc}" -O2 -Wall -I"$prefix/include" "$here/tcd/$t.c" "$prefix/lib/libtcd.a" -o "$build/$t" ||
        { echo "$t build failed" >&2; exit 1; }
done
otc_tcd="$out/$OTC_TCD_ID.tcd"
"$build/tcd_merge" "$tcd" "$run_tcd" "$otc_tcd" "$OTC_TCD_STAMP" "${OTC_ADDED[@]}" >/dev/null
got="$(sha256 "$otc_tcd")"
[[ "$got" == "$OTC_TCD_SHA256" ]] || { echo "$otc_tcd: SHA-256 $got, expected $OTC_TCD_SHA256 (the OTC TCD)" >&2; exit 1; }

dump() {   # TCD FIRST LAST OUT
    "$build/tcd_dump" "$1" "$2" "$3" >"$4.tsv"
    python3 "$here/tcd/tcd_json.py" "$1" "$2" "$3" "$4.tsv" "$4" >/dev/null
    rm -f "$4.tsv"
}
dump "$run_tcd" "$FIRST_YEAR" "$LAST_YEAR" "$out/congen-$FIRST_YEAR-$LAST_YEAR.json"
dump "$otc_tcd" "$FIRST_YEAR" "$LAST_YEAR" "$out/tcd-$FIRST_YEAR-$LAST_YEAR.json"
dump "$otc_tcd" "$FIT_FIRST_YEAR" "$LAST_YEAR" "$out/tcd-$FIT_FIRST_YEAR-$LAST_YEAR.json"

python3 - "$out" "$FIRST_YEAR" "$LAST_YEAR" "$CONGEN_URL" "$CONGEN_SHA256" "$LIBTCD_URL" "$LIBTCD_SHA256" \
    "$OTC_TCD_ID" "$SOURCE_TCD_SHA256" "$OTC_TCD_STAMP" "$FIT_FIRST_YEAR" "${OTC_ADDED[*]}" <<'EOF'
import hashlib, json, os, sys
out, first, last, curl, csha, lurl, lsha, otc_id, source_sha, stamp, fit_first, added = sys.argv[1:]
sha = lambda p: hashlib.sha256(open(os.path.join(out, p), "rb").read()).hexdigest()
names = {"congen_tcd": f"congen-{first}-{last}.tcd", "congen_dump": f"congen-{first}-{last}.json",
         "tcd_dump": f"tcd-{first}-{last}.json", "otc_tcd": f"{otc_id}.tcd", "fit_dump": f"tcd-{fit_first}-{last}.json",
         "congen_input": "congen_input+otc.txt", "otc_definitions": "otc_constituents.txt"}
prov = {"tool": {"congen": {"url": curl, "sha256": csha}, "libtcd": {"url": lurl, "sha256": lsha}},
        "command": f"congen -b {first} -e {last} -tcd {names['congen_tcd']} < {names['congen_input']}",
        "years": [int(first), int(last)],
        "otc_tcd": {"id": otc_id, "source_tcd": {"path": "data/harmonics-dwf-20251228-free.tcd", "sha256": source_sha},
                    "added": added.split(), "last_modified": f"{stamp} UTC",
                    "definitions_sha256": sha(names["otc_definitions"]),
                    "how": "tools/tcd/tcd_merge.c: the source TCD unchanged, then the added constituents with the "
                           "congen run's values; amplitude 0 at every station"},
        "files": {k: {"path": v, "sha256": sha(v)} for k, v in names.items()}}
tmp = os.path.join(out, ".PROVENANCE.json.tmp")
with open(tmp, "w") as fh:
    json.dump(prov, fh, indent=1, sort_keys=True)
    fh.write("\n")
os.replace(tmp, os.path.join(out, "PROVENANCE.json"))
print(json.dumps(prov["files"], sort_keys=True))
EOF
