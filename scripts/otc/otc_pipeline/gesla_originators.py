"""data/gesla/record_originators.csv: every GESLA record's originator, bound to its sources (L-NC).

    uv run python -m otc_pipeline.gesla_originators build --gesla-metadata M --uhslc-yaml D   # rewrite CSV + sidecar
    uv run python -m otc_pipeline.gesla_originators check --gesla-metadata M --uhslc-yaml D   # byte-for-byte check

Columns: record_id, contributor, gesla_originator (the GESLA-4.1 metadata ORIGINATOR, whitespace collapsed),
uhslc_data_source (UHSLC records only: the Data_Source of the UHSLC RQ station metadata; an FD record takes
the Data_Source of every RQ version of its station number whose span overlaps its own), uhslc_basis.

The sidecar data/gesla/record_originators.sources.json pins the SHA-256 of the GESLA metadata file, of the
UHSLC metadata snapshot (the sorted list of file names and their SHA-256) and of the CSV itself. The build
(gesla_licences.record_originators) refuses a CSV whose SHA-256 is not the pinned one, so the table can only
change by regenerating it from the pinned sources; neither source is in git (GESLA data stays outside the repo).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

__all__ = ["SIDECAR", "build_csv", "snapshot_sha256", "uhslc_station"]

_DATA = Path(__file__).resolve().parents[3] / "data" / "gesla"
CSV_PATH = _DATA / "record_originators.csv"
SIDECAR = _DATA / "record_originators.sources.json"
_TOP = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:")


def _scalar(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    return v


def uhslc_station(text: str) -> dict:
    """Data_Source (items joined with ' / ', whitespace collapsed), Date_Start and Date_End from a UHSLC
    rqds metadata yaml. Reads only these keys; the format is UHSLC's (block or plain list items)."""
    lines = text.splitlines()
    items: list[str] = []
    i = next((k for k, ln in enumerate(lines) if ln.startswith("Data_Source:")), None)
    if i is not None:
        rest = lines[i][len("Data_Source:"):].strip()
        if rest:
            items.append(_scalar(rest))
        cur: list[str] | None = None
        for ln in lines[i + 1:]:
            if _TOP.match(ln):
                break
            if ln.startswith("- "):
                if cur is not None:
                    items.append(" ".join(cur))
                head = ln[2:].strip()
                cur = [] if head in ("|-", "|", ">", ">-") else [_scalar(head)]
            elif cur is not None:
                cur.append(ln.strip())
        if cur is not None:
            items.append(" ".join(cur))
    src = " / ".join(re.sub(r"\s+", " ", s).strip() for s in items)

    def date(key):
        m = re.search(rf"^\s+{key}:\s*(.*)$", text, re.MULTILINE)
        return _scalar(m.group(1))[:10] if m else ""
    return {"source": src, "start": date("Date_Start"), "end": date("Date_End")}


def snapshot_sha256(yaml_dir: Path) -> tuple[str, int]:
    files = sorted(Path(yaml_dir).glob("*meta.yaml"))
    listing = "".join(f"{p.name}\t{hashlib.sha256(p.read_bytes()).hexdigest()}\n" for p in files)
    return hashlib.sha256(listing.encode()).hexdigest(), len(files)


def build_csv(gesla_metadata: Path, yaml_dir: Path) -> bytes:
    with open(gesla_metadata, encoding="utf-8-sig", newline="") as f:
        meta = {x["FILE NAME"]: x for x in csv.DictReader(f)}
    stations = {p.name[:-len("meta.yaml")]: uhslc_station(p.read_text(encoding="utf-8"))
                for p in sorted(Path(yaml_dir).glob("*meta.yaml"))}
    versions: dict[str, list[tuple[str, dict]]] = {}
    for code, y in stations.items():
        versions.setdefault(re.sub(r"[A-Z]$", "", code), []).append((code, y))

    def con(r):
        return meta[r]["CONTRIBUTOR (ABBREVIATED)"]

    def gorg(r):
        return re.sub(r"\s+", " ", meta[r]["ORGINATOR"].replace("\\u2013", "-")).strip()

    def uhslc(r):
        code = meta[r]["SITE CODE"].strip()
        if con(r) == "UHSLC_RQ":
            y = stations.get(code.upper())
            return (y["source"], f"rqds {code.upper()}") if y else ("", "no rqds metadata")
        if con(r) == "UHSLC_FD":
            s = meta[r]["START DATE/TIME"][:10].replace("/", "-")
            e = meta[r]["END DATE/TIME"][:10].replace("/", "-")
            num = re.sub(r"[A-Za-z]$", "", code).upper()
            hit = [(c, y) for c, y in versions.get(num, [])
                   if y["start"] and y["start"] <= e and (not y["end"] or y["end"] >= s or y["end"] == "missing")]
            if hit:
                return (" | ".join(f"{c}: {y['source']}" for c, y in hit),
                        f"fd span {s}..{e} overlaps rqds {','.join(c for c, _ in hit)}")
            return ("", f"fd span {s}..{e}: no rqds version of {num} overlaps")
        return ("", "")

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["record_id", "contributor", "gesla_originator", "uhslc_data_source", "uhslc_basis"])
    for r in sorted(meta):
        u, basis = uhslc(r)
        w.writerow([r, con(r), gorg(r), u, basis])
    return buf.getvalue().encode("utf-8")


def _sources(gesla_metadata: Path, yaml_dir: Path, csv_bytes: bytes) -> dict:
    snap, n = snapshot_sha256(yaml_dir)
    return {"generator": "otc_pipeline.gesla_originators build",
            "gesla_metadata": {"file": "GESLA4-1_ALL.csv",
                               "sha256": hashlib.sha256(Path(gesla_metadata).read_bytes()).hexdigest()},
            "uhslc_metadata_snapshot": {"source": "https://uhslc.soest.hawaii.edu/rqds/metadata_yaml/<code>meta.yaml",
                                        "files": n, "sha256": snap},
            "csv_sha256": hashlib.sha256(csv_bytes).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="otc_pipeline.gesla_originators")
    ap.add_argument("cmd", choices=("build", "check"))
    ap.add_argument("--gesla-metadata", type=Path, required=True)
    ap.add_argument("--uhslc-yaml", type=Path, required=True)
    a = ap.parse_args(argv)
    data = build_csv(a.gesla_metadata, a.uhslc_yaml)
    side = _sources(a.gesla_metadata, a.uhslc_yaml, data)
    if a.cmd == "build":
        CSV_PATH.write_bytes(data)
        SIDECAR.write_text(json.dumps(side, indent=1) + "\n")
        print(f"wrote {CSV_PATH} ({side['csv_sha256'][:12]}) and {SIDECAR}")
        return 0
    pinned = json.loads(SIDECAR.read_text())
    bad = [k for k in ("gesla_metadata", "uhslc_metadata_snapshot") if pinned[k]["sha256"] != side[k]["sha256"]]
    if bad or CSV_PATH.read_bytes() != data:
        print(f"MISMATCH: inputs {bad or 'pinned'}; csv {'differs' if CSV_PATH.read_bytes() != data else 'same'}")
        return 1
    print("record_originators.csv matches its pinned sources byte for byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
