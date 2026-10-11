"""The JSON dump of a TCD from tcd_dump's lines: the file scripts/gesla/dump_tcd.rb writes, byte for byte.

    python3 tcd_json.py FILE.tcd FIRST_YEAR LAST_YEAR LINES.tsv OUT.json

Keys and layout are dump_tcd.rb's: {"aliases", "constituents": {name: {"f", "speed", "v0u"}} sorted by name,
"tcd_sha256" (of FILE.tcd), "years"}, compact separators. Numbers are the stored values (tcd_dump prints them to
the TCD's own resolution) written in their shortest form, as Ruby's JSON.generate writes a Float. A test checks the
output equals dump_tcd.rb's on data/harmonics-dwf-20251228-free.tcd. Standard library only (python3).
"""
import hashlib
import json
import os
import sys

# dump_tcd.rb GeslaTcdDump::TICON_TO_TCD_NAMES (TICON spellings the TCD spells differently); a test checks they agree.
ALIASES = {"EP2": "EPS2", "LM2": "LDA2", "MI2": "MU2", "MKS": "MKS2", "NI2": "NU2"}


def number(text):
    x = float(text)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite number: {text!r}")
    return x


def dump(tcd_path, first, last, lines):
    n = last - first + 1
    consts = {}
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        name, speed, v0u, f = line.rstrip("\n").split("\t")
        v0u, f = [number(x) for x in v0u.split(",")], [number(x) for x in f.split(",")]
        if len(v0u) != n or len(f) != n:
            raise ValueError(f"line {i} ({name}): {len(v0u)} V0+u and {len(f)} f values, expected {n}")
        if name in consts:
            raise ValueError(f"line {i}: constituent {name} twice")
        consts[name] = {"f": f, "speed": number(speed), "v0u": v0u}
    if not consts:
        raise ValueError("no constituents")
    with open(tcd_path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    return {"aliases": dict(sorted(ALIASES.items())), "constituents": dict(sorted(consts.items())),
            "tcd_sha256": sha, "years": [first, last]}


def main(argv):
    if len(argv) != 6:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    tcd, first, last, tsv, out = argv[1], int(argv[2]), int(argv[3]), argv[4], argv[5]
    with open(tsv) as fh:
        table = dump(tcd, first, last, fh.readlines())
    tmp = f"{out}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(table, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, out)
    print(f"years {first}-{last} n={len(table['constituents'])} tcd_sha256={table['tcd_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
