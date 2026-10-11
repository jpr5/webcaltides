"""A synthetic congen run for the synthetic build tests: a run directory as congen_tables.sh writes it
(PROVENANCE.json with the pinned tools, a stand-in .tcd file, a congen dump and a TCD dump for
1700-2100) with the speeds of a small fit table and made-up yearly values, loaded by
astro_tables.load_congen_run like a real one. The two dumps hold the same values. No TCD values are
in git. The directories live under one temp dir made here and removed at exit."""
from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from gesla.tcd_table import TcdTable

from otc_pipeline import astro_tables as at

_ROOT: Path | None = None
_CACHE: dict = {}


def _root() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp(prefix="kp0b-synthetic-congen-"))
        made = _ROOT
        atexit.register(lambda: shutil.rmtree(made, ignore_errors=True))
    return _ROOT


def _dump_data(speeds: dict[str, float], sha: str, aliases: dict) -> dict:
    n = at.LAST_YEAR - at.FIRST_YEAR + 1
    cons = {name: {"speed": s, "v0u": [round((37.25 * i + 11.0 * k) % 360.0, 2) for i in range(n)],
                   "f": [round(1.0 + 0.001 * ((i + k) % 21 - 10), 4) for i in range(n)]}
            for k, (name, s) in enumerate(sorted(speeds.items()))}
    return {"years": [at.FIRST_YEAR, at.LAST_YEAR], "tcd_sha256": sha, "aliases": aliases, "constituents": cons}


def write_run(d: Path, table: TcdTable, *, edit=None) -> Path:
    """Write a run directory for `table`'s constituents and TCD SHA-256 into d. `edit(prov, congen, tcd)`
    may change the PROVENANCE dict and the two dump dicts before they are written and hashed."""
    d.mkdir(parents=True, exist_ok=True)
    tcd_bytes = b"synthetic congen TCD for " + table.tcd_sha256.encode()
    (d / at.CONGEN_TCD).write_bytes(tcd_bytes)
    run_sha = hashlib.sha256(tcd_bytes).hexdigest()
    speeds = {n: c.speed for n, c in table.constituents.items()}
    congen = _dump_data(speeds, run_sha, dict(table.aliases))
    tcd = _dump_data(speeds, table.tcd_sha256, dict(table.aliases))
    prov = {"tool": {"congen": {"url": "https://flaterco.com/files/xtide/congen-1.7-r2.tar.xz", "sha256": at.CONGEN_SHA256},
                     "libtcd": {"url": "https://flaterco.com/files/xtide/libtcd-2.2.7-r3.tar.xz", "sha256": at.LIBTCD_SHA256}},
            "command": f"congen -b {at.FIRST_YEAR} -e {at.LAST_YEAR} -tcd {at.CONGEN_TCD} < congen-1.7/congen_input.txt",
            "years": [at.FIRST_YEAR, at.LAST_YEAR], "files": {"congen_tcd": {"path": at.CONGEN_TCD, "sha256": run_sha}}}
    if edit:
        edit(prov, congen, tcd)
    for key, data in (("congen_dump", congen), ("tcd_dump", tcd)):
        name = at.CONGEN_RUN_FILES[key]
        (d / name).write_text(json.dumps(data))
        prov["files"].setdefault(key, {"path": name, "sha256": hashlib.sha256((d / name).read_bytes()).hexdigest()})
    (d / "PROVENANCE.json").write_text(json.dumps(prov))
    return d


def congen_run(table: TcdTable) -> at.CongenRun:
    """The loaded synthetic run for the constituents and the TCD SHA-256 of a fit table (cached)."""
    key = (table.tcd_sha256, tuple(sorted((n, c.speed) for n, c in table.constituents.items())))
    if key not in _CACHE:
        d = write_run(Path(tempfile.mkdtemp(dir=_root())), table)
        _CACHE[key] = at.load_congen_run(d, tcd_sha256=table.tcd_sha256)
    return _CACHE[key]


def kp0b_tables(run, names: Iterable[str], table_id: str) -> list[dict]:
    return [at.astro_table(*run, names, table_id=table_id)]
