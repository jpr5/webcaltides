"""Run pinned XTide on unchanged folded inputs and record its New Year events."""
from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from otc_pipeline.astro_tables import NAME_TO_TCD


PUBLISHED_SHA256 = "33f97cb73ac25a0fec273950c0b85ff55ddbe7edf4448e80ea8d5b3d59b0aeb1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log):
    result = subprocess.run(command, capture_output=True, text=True)
    log.write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "own-tcd", "published-tcd", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--image", default="otc-kp1t-xtide:2.16")
    args = parser.parse_args()
    doc = json.loads(args.input.read_text())
    if digest(args.own_tcd) != doc["astro_table"]["tables_sha256"]:
        raise ValueError("own TCD does not match the accepted strict-engine input")
    if digest(args.published_tcd) != PUBLISHED_SHA256:
        raise ValueError("New Year comparison requires the unchanged published tcd-20251228")
    args.out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.own_tcd, args.out / "same-inputs.tcd")
    shutil.copyfile(args.published_tcd, args.out / "published.tcd")
    lines = []
    for folded in doc["folded_calls"]:
        call = folded["call"]
        offsets = call["offsets"]
        if offsets["time_offset_high_min"] != offsets["time_offset_low_min"] or \
                offsets["height_offset_high"] != offsets["height_offset_low"]:
            raise ValueError("XTide comparison station does not have equal offsets")
        cons = folded["constant_set"]["constituents"]
        lines.append(f"{folded['station']} {folded['reference_station']} {call['chart_datum_term']:.17g} "
                     f"{offsets['time_offset_high_min']} {offsets['height_adjusted_type']} "
                     f"{offsets['height_offset_high']:.17g} {len(cons)}")
        for c in cons:
            lines.append(f"{NAME_TO_TCD.get(c['name'], c['name'])} "
                         f"{c['amplitude_m']:.17g} {c['phase_deg']:.17g}")
    (args.out / "same-inputs.tsv").write_text("\n".join(lines) + "\n")
    mount = ["-v", f"{args.out.resolve()}:/out"]
    source = Path(__file__).with_name("same_inputs.c").resolve()
    run(["docker", "run", "--rm", "--entrypoint", "cc", *mount, "-v", f"{source}:/source.c:ro",
         args.image, "-Wall", "-Wextra", "-Werror", "/source.c", "-o", "/out/same_inputs", "-ltcd", "-lm"],
        args.out / "compile.log")
    run(["docker", "run", "--rm", "--entrypoint", "/out/same_inputs", *mount, args.image,
         "/out/same-inputs.tcd", "/out/same-inputs.tsv"], args.out / "records.log")
    curves = []
    for folded in doc["folded_calls"]:
        sid = folded["station"]
        output = args.out / f"{sid}-curve.csv"
        output.unlink(missing_ok=True)  # XTide appends CSV output.
        run(["docker", "run", "--rm", *mount, "-e", "HFILE_PATH=/out/same-inputs.tcd", args.image,
             "-l", f"OTC-KP1T-{sid}-folded", "-b", "2026-10-01 00:00", "-e", "2026-11-01 00:00",
             "-m", "r", "-f", "c", "-s", "00:06", "-u", "m", "-z", "-o", f"/out/{output.name}"],
            args.out / f"{sid}-run.log")
        curves.append({"station": sid, "csv": str(output), "sha256": digest(output), "call": folded["call"]})
    new_year = args.out / "pensacola-2052-2053.csv"
    new_year.unlink(missing_ok=True)
    run(["docker", "run", "--rm", *mount, "-e", "HFILE_PATH=/out/published.tcd", args.image,
         "-l", "Pensacola, Florida", "-b", "2052-12-31 00:00", "-e", "2053-01-02 00:00",
         "-m", "p", "-f", "c", "-z", "-u", "m", "-o", f"/out/{new_year.name}"],
        args.out / "new-year-run.log")
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    with new_year.open() as stream:
        records = list(csv.DictReader(stream))
    tide_events = []
    for row in records:
        if row["Event"] not in ("High Tide", "Low Tide"):
            continue
        stamp = datetime.strptime(f"{row['Date']} {row['Time']}", "%Y-%m-%d %I:%M %p UTC").replace(tzinfo=UTC)
        value, unit = row["Value"].split()
        if unit != "m":
            raise ValueError("New Year event did not use metres")
        tide_events.append({"time": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "type": "high" if row["Event"] == "High Tide" else "low",
                            "height_m": float(value)})
    report = {"image": args.image, "image_id": image, "input_sha256": digest(args.input),
              "source_own_tcd_sha256": digest(args.own_tcd), "comparison_tcd_sha256": digest(args.out / "same-inputs.tcd"),
              "published_tcd_sha256": PUBLISHED_SHA256, "curves": curves,
              "new_year": {"station": "Pensacola, Florida", "start": "2052-12-31T00:00:00Z",
                           "stop": "2053-01-02T00:00:00Z", "csv": str(new_year), "sha256": digest(new_year),
                           "records": records, "tide_events": tide_events}}
    (args.out / "xtide-report.json").write_text(json.dumps(report, sort_keys=True, indent=1) + "\n")
    print(json.dumps({"curves": len(curves), "new_year_tide_events": len(tide_events),
                      "image_id": image}))


if __name__ == "__main__":
    main()
