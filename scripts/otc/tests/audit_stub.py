"""TEST ONLY: a time-base audit output dir that passes timebase_audit.check_audit_outputs, around given
rows (or a copy of a real timebase.csv). A real audit made before the crash check (85c7076) has no
weak_crashes and is refused by the gate; the tests that read such an audit's rows put them in a stub.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Iterable, Mapping

COLUMNS = ["file_id", "time_base_status", "time_base_code", "published_time_base", "comparator_kind", "comparator",
           "official_lag", "lag_median", "months_bad", "reason", "time_base_reason", "weak_eot20", "weak_served",
           "weak_dst"]


def stub_audit(out: Path, rows: Iterable[Mapping] | None = None, *, csv_from: Path | None = None,
               commit: str = "1efca1c676a0338056d328632d283675fb63d62e",
               finished: str = "2026-10-10T08:02:12+00:00", **manifest) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "timebase.csv"
    if csv_from is not None:
        shutil.copyfile(csv_from, path)
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows or ():
                w.writerow({c: r.get(c, "") for c in COLUMNS})
    m = {"status": "complete", "commit": commit, "dirty": False, "finished": finished, "weak_crashes": [],
         "outputs": {"timebase.csv": hashlib.sha256(path.read_bytes()).hexdigest()}}
    m.update(manifest)
    (out / "timebase_manifest.json").write_text(json.dumps(m))
    return out
