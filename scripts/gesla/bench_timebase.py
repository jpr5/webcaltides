"""Time the GESLA time-base audit on a subset and print the CPU time of each phase.

Usage (from scripts/gesla, with the OTC_WORK environment of a real run):

    nice -n 19 .venv/bin/python bench_timebase.py --out-dir /tmp/bench \\
        --only newport-8452660-usa-noaa borkumtg-bor-deu-cmems texel_noordzee-tex-nld-rws

Every argument goes to `python -m gesla.timebase_audit` unchanged, except:
- --out-dir is required and must lie outside $OTC_WORK/builds: a bench never writes a real build
  (its residue.jsonl goes to the out-dir too, never to a build's).
- the manifest it leaves is marked status "benchmark", with "benchmark": {"controls": "skipped" or
  "run"}, so check_audit_outputs refuses it: a subset with no controls never passes for an audit.
- --workers defaults to 1 here: the phases are timed with time.process_time(), which counts only
  this process. With more workers, the CPU column shows the parent only; use the wall column.
- --with-controls runs the positive controls (every real build does); by default they are skipped,
  since they are a fixed cost per run and do not depend on the subset.

The phases are the audit's own log lines (round 0, round 0 cross-check, round <n>, final
cross-check, weak evidence); "startup" is everything before round 0, "write" everything after the
last phase. The audit's own output is printed as usual; the table comes at the end.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import gesla.timebase_audit as ta
import gesla.timebase_verdict as tv
from gesla.read_gesla import otc_work

PHASES = ("round 0 cross-check:", "final cross-check:", "weak evidence:", "round ")


def _phase(msg: str) -> str | None:
    """The phase a log line starts, or None (progress lines and other messages)."""
    if "records done" in msg:
        return None
    for p in PHASES:
        if msg.startswith(p):
            return msg.split(":", 1)[0]
    return None


def _no_controls(*_args, **_kwargs) -> list:
    """Stands in for run_positive_controls when --with-controls is not given."""
    return []


def _out_dir(argv: list[str]) -> Path:
    """The --out-dir argument (either form, the last one wins as in the audit); SystemExit if it is
    missing, abbreviated, or inside $OTC_WORK/builds. The audit's argparse takes a unique prefix
    (--out, --ou, --out-d) as --out-dir, so an abbreviation would bypass this check: refused."""
    for a in argv:
        key = a.split("=", 1)[0]
        if key.startswith("--") and len(key) > 2 and key != "--out-dir" and "--out-dir".startswith(key):
            raise SystemExit(f"bench_timebase: {key} would be read as an abbreviation of --out-dir; spell --out-dir")
    ap = argparse.ArgumentParser(prog="bench_timebase.py", add_help=False, allow_abbrev=False)
    ap.add_argument("--out-dir")
    out = ap.parse_known_args(argv)[0].out_dir
    if not out:
        raise SystemExit("bench_timebase: pass --out-dir (outside $OTC_WORK/builds): a bench must not write a real build")
    p = Path(out).expanduser().resolve()
    builds = (otc_work() / "builds").expanduser().resolve()
    if p == builds or p.is_relative_to(builds):
        raise SystemExit(f"bench_timebase: --out-dir {out} is inside $OTC_WORK/builds ({builds}): "
                         "a bench must not write a real build")
    return p


def _mark_benchmark(out: Path, with_controls: bool) -> None:
    """Rewrite the audit's manifest (any status it reached) as a benchmark, which consumers refuse."""
    mp = out / ta.MANIFEST
    if not mp.is_file():
        return
    m = json.loads(mp.read_text())
    m["benchmark"] = {"controls": "run" if with_controls else "skipped"}
    m["audit_status"] = m.get("status")
    m["status"] = "benchmark"
    mp.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    out = _out_dir(argv)
    with_controls = "--with-controls" in argv
    argv = [a for a in argv if a != "--with-controls"]
    if not any(a == "--workers" or a.startswith("--workers=") for a in argv):
        argv = argv + ["--workers", "1"]
    marks: list[tuple[str, float, float]] = [("startup", time.process_time(), time.monotonic())]
    log = ta._log

    def timed_log(msg: str) -> None:
        p = _phase(msg)
        if p is not None:
            marks.append((p, time.process_time(), time.monotonic()))
        log(msg)

    controls = tv.run_positive_controls
    ta._log = timed_log
    if not with_controls:
        tv.run_positive_controls = _no_controls
    try:
        rc = ta.main(argv)
    except BaseException as err:
        ta._log = log
        tv.run_positive_controls = controls
        try:
            _mark_benchmark(out, with_controls)
        except Exception as mark_err:     # never hide the audit's own error: re-raise it, chained
            raise err from mark_err
        raise
    ta._log = log
    tv.run_positive_controls = controls
    _mark_benchmark(out, with_controls)   # a failure here raises: an unmarked complete manifest must not stand
    marks.append(("end", time.process_time(), time.monotonic()))
    print("\nphase                    cpu_s     wall_s")
    names = [m[0] for m in marks[:-1]]
    names[-1] = f"{names[-1]} + write" if names[-1] != "startup" else names[-1]
    for name, (_, c0, w0), (_, c1, w1) in zip(names, marks, marks[1:]):
        print(f"{name:<22} {c1 - c0:9.1f} {w1 - w0:10.1f}")
    sib = sum(c1 - c0 for (n, c0, _), (_, c1, _) in zip(marks, marks[1:])
              if "cross-check" in n or (n.startswith("round ") and n != "round 0"))
    print(f"{'sibling phases':<22} {sib:9.1f}")
    print(f"{'total':<22} {marks[-1][1] - marks[0][1]:9.1f} {marks[-1][2] - marks[0][2]:10.1f}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
