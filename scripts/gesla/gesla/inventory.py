#!/usr/bin/env python3
"""BP S2: GESLA record inventory and the licence exclusion switch.

    python3 scripts/gesla/gesla/inventory.py [--version 4.1] [--cache DIR] [--roles FILE]
                                             [--zip FILE | --no-intervals] [--out FILE] [--counts]

Reads the GESLA metadata CSV from the verified local cache ($OTC_WORK/inputs/gesla/<version>/,
filled by scripts/gesla/fetch_mirror.rb), checks its SHA-256 and size against the cache's
MANIFEST.json, and gives every record its contributor's role and licence class from
data/gesla/contributor_roles.csv.  A contributor missing from that table is treated as a
redistributor with licence class `unlisted` (BP §9) and is named on stderr.

The metadata CSV has CR line endings, and its header runs "OVERALL RECORD QUALITY" and
"DOWNLOAD LINK" together (PP §1.1); both are handled, and any other header is refused.

GESLA metadata does not give the sampling interval.  With the cached zip present, the interval is
the modal step (seconds) of each record's first samples, read from the start of each member.
The sampling type (instantaneous or mean) is not stated anywhere in GESLA, so the inventory has no
column for it; the time-base audit (BP S4) measures it per record.  The originator is not repeated
here either (it is in the metadata CSV); the contributor's role comes from the roles table.

GESLA_EXCLUDE_LICENCE_CLASSES (comma-separated licence classes, default empty) removes records of
those classes from the output.  An unknown class name is an error, never a silent no-op.

--out writes the inventory CSV (default data/gesla/<version>/inventory.csv); --counts prints the
record count per licence class and how many records the switch excludes.
"""

import argparse
import collections
import csv
import datetime
import hashlib
import io
import json
import os
import pathlib
import sys
import zipfile

LICENCE_CLASSES = ("research_consultancy", "research_only", "unlisted")
ROLES = ("originator", "redistributor")
SWITCH = "GESLA_EXCLUDE_LICENCE_CLASSES"
METADATA_PATH = "metadata/GESLA4-1_ALL.csv"

HEADER = [
    "FILE NAME", "SITE NAME", "SITE CODE", "COUNTRY", "CONTRIBUTOR (ABBREVIATED)", "CONTRIBUTOR (FULL)",
    "CONTRIBUTOR WEBSITE", "CONTRIBUTOR CONTACT", "ORGINATOR", "ORIGINATOR WEBSITE", "ORIGINATOR CONTACT",
    "LATITUDE", "LONGITUDE", "COORDINATE SYSTEM", "START DATE/TIME", "END DATE/TIME", "NUMBER OF YEARS",
    "TIME ZONE HOURS", "DATUM INFORMATION", "INSTRUMENT", "PRECISION", "NULL VALUE", "GAUGE TYPE",
    "OVERALL RECORD QUALITY", "DOWNLOAD LINK",
]
BROKEN_TAIL = "OVERALL RECORD QUALITYDOWNLOAD LINK"

OUT_FIELDS = [
    "file_id", "contributor", "role", "licence_class", "gauge_type", "interval_s", "start", "end", "years", "lat", "lon",
]

REPO = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_ROLES = REPO / "data" / "gesla" / "contributor_roles.csv"
INTERVAL_SAMPLES = 200
INTERVAL_READ_BYTES = 256 * 1024


class InventoryError(Exception):
    pass


def _timestamp(value):
    # "1921/01/01 01:00:00" -> "1921-01-01T01:00:00"
    date, _, time = value.strip().partition(" ")
    return f"{date.replace('/', '-')}T{time}"


def parse_metadata(path):
    """Return one dict per record of the GESLA metadata CSV, in file order."""
    text = pathlib.Path(path).read_bytes().decode("utf-8")
    lines = [line for line in text.replace("\r\n", "\r").replace("\n", "\r").split("\r") if line.strip()]
    if not lines:
        raise InventoryError(f"{path}: empty metadata file")
    header = next(csv.reader([lines[0]]))
    if header[-1] == BROKEN_TAIL:
        header = header[:-1] + ["OVERALL RECORD QUALITY", "DOWNLOAD LINK"]
    if header != HEADER:
        raise InventoryError(f"{path}: unexpected header {header!r}")
    records = []
    for lineno, fields in enumerate(csv.reader(lines[1:]), start=2):
        if len(fields) != len(HEADER):
            raise InventoryError(f"{path}: line {lineno} ({fields[0] if fields else ''}) has {len(fields)} fields, expected {len(HEADER)}")
        f = dict(zip(HEADER, fields))
        try:
            records.append({
                "file_id": f["FILE NAME"].strip(),
                "contributor": f["CONTRIBUTOR (ABBREVIATED)"].strip(),
                "contributor_full": f["CONTRIBUTOR (FULL)"].strip(),
                "originator": f["ORGINATOR"].strip(),
                "gauge_type": f["GAUGE TYPE"].strip(),
                "start": _timestamp(f["START DATE/TIME"]),
                "end": _timestamp(f["END DATE/TIME"]),
                "years": int(f["NUMBER OF YEARS"]),
                "lat": float(f["LATITUDE"]),
                "lon": float(f["LONGITUDE"]),
                "download_link": f["DOWNLOAD LINK"].strip(),
            })
        except ValueError as exc:
            raise InventoryError(f"{path}: line {lineno} ({f['FILE NAME']}): {exc}") from exc
    ids = [r["file_id"] for r in records]
    dupes = sorted(i for i, n in collections.Counter(ids).items() if n > 1)
    if dupes:
        raise InventoryError(f"{path}: duplicate file ids {dupes[:5]}")
    return records


def verify_metadata(path, manifest_path, member=METADATA_PATH):
    """Check the metadata file's size and SHA-256 against its MANIFEST.json entry."""
    path = pathlib.Path(path)
    manifest = json.loads(pathlib.Path(manifest_path).read_text())
    entry = next((e for e in manifest.get("files", []) if e.get("path") == member), None)
    if entry is None:
        raise InventoryError(f"{manifest_path}: no entry for {member}")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != entry["sha256"] or len(data) != entry["size"]:
        raise InventoryError(f"{path}: SHA-256/size mismatch with {manifest_path} ({sha} {len(data)} != {entry['sha256']} {entry['size']})")
    return sha


def load_roles(path=DEFAULT_ROLES):
    roles = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            c = row["contributor"].strip()
            if row["role"] not in ROLES or row["licence_class"] not in LICENCE_CLASSES:
                raise InventoryError(f"{path}: bad role or licence class for {c}: {row}")
            if c in roles:
                raise InventoryError(f"{path}: {c} listed twice")
            roles[c] = {"role": row["role"], "licence_class": row["licence_class"]}
    return roles


def build_inventory(records, roles, intervals=None, warn=None):
    intervals = intervals or {}
    unknown = set()
    out = []
    for r in records:
        entry = roles.get(r["contributor"])
        if entry is None:
            unknown.add(r["contributor"])
            entry = {"role": "redistributor", "licence_class": "unlisted"}
        interval = intervals.get(r["file_id"])
        out.append({
            "file_id": r["file_id"],
            "contributor": r["contributor"],
            "role": entry["role"],
            "licence_class": entry["licence_class"],
            "gauge_type": r["gauge_type"],
            "interval_s": "" if interval is None else interval,
            "start": r["start"],
            "end": r["end"],
            "years": r["years"],
            "lat": r["lat"],
            "lon": r["lon"],
        })
    if unknown and warn:
        warn(f"contributors not in the roles table (treated as unlisted redistributors): {', '.join(sorted(unknown))}")
    return out


def excluded_classes(env=os.environ):
    raw = env.get(SWITCH, "") or ""
    classes = frozenset(c.strip() for c in raw.split(",") if c.strip())
    bad = sorted(classes - set(LICENCE_CLASSES))
    if bad:
        raise InventoryError(f"{SWITCH}: unknown licence class(es) {', '.join(bad)}; known: {', '.join(LICENCE_CLASSES)}")
    return classes


def apply_switch(inventory, excluded):
    return [r for r in inventory if r["licence_class"] not in excluded]


def class_counts(inventory):
    return dict(collections.Counter(r["licence_class"] for r in inventory))


def _interval(fh):
    steps = collections.Counter()
    prev = None
    n = 0
    for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
        if raw.startswith("#") or not raw.strip():
            continue
        parts = raw.split()
        if len(parts) < 2:
            continue
        y, mo, d = (int(x) for x in parts[0].split("/"))
        h, mi, s = (int(float(x)) for x in parts[1].split(":"))
        # seconds on the proleptic Gregorian day count; only differences between samples are used
        t = datetime.date(y, mo, d).toordinal() * 86400 + h * 3600 + mi * 60 + s
        if prev is not None and t > prev:
            steps[t - prev] += 1
        prev = t
        n += 1
        if n >= INTERVAL_SAMPLES:
            break
    return steps.most_common(1)[0][0] if steps else None


def intervals_from_zip(zip_path, file_ids):
    """Modal step (seconds) of each record's first samples, read from the start of each member."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for fid in file_ids:
            if fid not in names:
                raise InventoryError(f"{zip_path}: no member {fid}")
            with z.open(fid) as fh:
                out[fid] = _interval(io.BytesIO(fh.read(INTERVAL_READ_BYTES)))
    return out


def write_inventory(inventory, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(inventory, key=lambda r: r["file_id"]):
            w.writerow({**r, "lat": f"{r['lat']:.6f}", "lon": f"{r['lon']:.6f}"})
    tmp.replace(path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--version", default="4.1")
    p.add_argument("--cache", help="GESLA cache dir (default $OTC_WORK/inputs/gesla/<version>)")
    p.add_argument("--roles", default=str(DEFAULT_ROLES))
    p.add_argument("--zip", help="GESLA zip for intervals (default <cache>/GESLA<version>_ALL.zip)")
    p.add_argument("--no-intervals", action="store_true")
    p.add_argument("--out", help="inventory CSV path (default data/gesla/<version>/inventory.csv)")
    p.add_argument("--counts", action="store_true", help="print class counts; write nothing unless --out is given")
    a = p.parse_args(argv)

    work = pathlib.Path(os.environ.get("OTC_WORK", pathlib.Path.home() / ".local/share/opentideconstants/work"))
    cache = pathlib.Path(a.cache) if a.cache else work / "inputs" / "gesla" / a.version
    meta = cache / METADATA_PATH
    try:
        excluded = excluded_classes()
        sha = verify_metadata(meta, cache / "MANIFEST.json")
        records = parse_metadata(meta)
        roles = load_roles(a.roles)
        intervals = None
        if not a.no_intervals and (a.out or not a.counts):
            zpath = pathlib.Path(a.zip) if a.zip else cache / f"GESLA{a.version}_ALL.zip"
            intervals = intervals_from_zip(zpath, [r["file_id"] for r in records])
        inv = build_inventory(records, roles, intervals, warn=lambda m: print(f"inventory: {m}", file=sys.stderr))
        kept = apply_switch(inv, excluded)
    except (InventoryError, OSError) as exc:
        print(f"inventory: {exc}", file=sys.stderr)
        return 1

    if a.counts:
        print(f"metadata {meta.name} sha256 {sha}")
        print(f"records {len(inv)}")
        for c in LICENCE_CLASSES:
            print(f"class {c} {class_counts(inv).get(c, 0)}")
        print(f"{SWITCH}={','.join(sorted(excluded))}")
        print(f"excluded {len(inv) - len(kept)}")
        print(f"output {len(kept)}")
    if a.out or not a.counts:
        out = pathlib.Path(a.out) if a.out else REPO / "data" / "gesla" / a.version / "inventory.csv"
        write_inventory(kept, out)
        print(f"wrote {len(kept)} records to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
