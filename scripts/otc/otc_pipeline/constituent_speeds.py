"""Constituent speeds as the release's astro tables have them (KP0b), for the adapters that publish a
source's own constituent names (NOAA, NOAA currents, Kartverket).

Build check 7 needs every constituent of a set to have a row in its convention's astro table, with the
set's speed equal to the row's within 1e-7 deg/h. KP0b makes one row per release constituent name from
the congen run, after checking it equal to the TCD; a name maps to its TCD constituent by NAME_TO_TCD,
then by the TCD dump's own aliases. So an adapter:

- writes, for a constituent, the speed of its row (`TableSpeeds.speed`), matched by its own name;
- refuses (SpeedDeviation) a source speed more than SPEED_FLAG_DEG_PER_HOUR from that row: a different
  constituent under a known name is never silently given the table's speed;
- leaves out a constituent with no row (`speed` is None) as a dropped constituent with reason "other"
  and detail NO_ASTRO_ROW: its f and u cannot be checked, and none are made up.

The speeds come from the TCD dump (`from_tcd_dump`), which KP0b checks equal to the congen run's within
1e-7 deg/h. A combined build gives every adapter the dump its fits and astro table use (combine.load_agency
`tcd_path`) and stops when a source read another (build_otc.check_one_table). NAME_TO_TCD is KP0b's (otc_pipeline.astro_tables.NAME_TO_TCD on blitz/otc-gesla/KP0b at
336cc19); the two must stay the same when the branches merge.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

__all__ = ["NAME_TO_TCD", "NO_ASTRO_ROW", "SPEED_FLAG_DEG_PER_HOUR", "SpeedDeviation", "TableSpeeds",
           "dropped_no_row"]

NAME_TO_TCD = {"LAM2": "LDA2", "RHO": "RHO1"}   # = KP0b astro_tables.NAME_TO_TCD (336cc19)
SPEED_FLAG_DEG_PER_HOUR = 1e-4
NO_ASTRO_ROW = "no checkable astro row"


class SpeedDeviation(ValueError):
    """A source speed differs from its astro row's by more than SPEED_FLAG_DEG_PER_HOUR."""


def dropped_no_row(name: str) -> dict:
    """The dropped_constituents entry for a constituent with no astro row."""
    return {"name": name, "dropped_reason": "other", "detail": NO_ASTRO_ROW}


class TableSpeeds:
    """Release constituent name -> the speed of its astro-table row (deg/h), or None for no row."""

    def __init__(self, speeds: Mapping[str, float], aliases: Mapping[str, str] | None = None,
                 tcd_sha256: str | None = None, dump_sha256: str | None = None):
        self.speeds = dict(speeds)
        self.aliases = dict(aliases or {})
        self.tcd_sha256 = tcd_sha256
        self.dump_sha256 = dump_sha256      # the SHA-256 of the dump file read (from_tcd_dump), else None

    @classmethod
    def from_tcd_dump(cls, path: Path | None = None) -> "TableSpeeds":
        """From the TCD dump at `path`, else gesla.tcd_table.dump_path() (a combined build passes its own)."""
        from gesla.tcd_table import TcdTable, dump_path
        dump = Path(path or dump_path())
        raw = dump.read_bytes()
        table = TcdTable(json.loads(raw))
        return cls({n: c.speed for n, c in table.constituents.items()}, table.aliases, table.tcd_sha256,
                   hashlib.sha256(raw).hexdigest())

    @classmethod
    def from_json(cls, path: Path) -> "TableSpeeds":
        """A saved extract of the TCD dump: {"speeds": {name: deg/h}, "aliases": {...}, "tcd_sha256": ...}."""
        d = json.loads(Path(path).read_text())
        return cls(d["speeds"], d.get("aliases"), d.get("tcd_sha256"))

    def tcd_name(self, name: str) -> str | None:
        mapped = NAME_TO_TCD.get(name, name)
        mapped = self.aliases.get(mapped, mapped)
        return mapped if mapped in self.speeds else None

    def speed(self, name: str, source_speed: float | None = None, *, where: str = "") -> float | None:
        """The row speed for `name`, or None when the release has no row for it. Raises SpeedDeviation
        when `source_speed` is more than SPEED_FLAG_DEG_PER_HOUR from it."""
        n = self.tcd_name(name)
        if n is None:
            return None
        speed = self.speeds[n]
        if source_speed is not None and abs(float(source_speed) - speed) > SPEED_FLAG_DEG_PER_HOUR:
            raise SpeedDeviation(f"{where}{name}: source speed {source_speed} deg/h differs from the astro row's "
                                 f"{speed} ({n}) by more than {SPEED_FLAG_DEG_PER_HOUR}")
        return speed
