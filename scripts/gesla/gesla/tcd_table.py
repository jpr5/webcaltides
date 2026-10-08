"""Load the TCD dump written by scripts/gesla/dump_tcd.rb.

The dump holds, per constituent of the TCD the harmonics engine reads, the speed
(deg/h) and, for every year of its range, V0+u (deg, Jan 1 00:00 UTC) and f. The
fit uses these values so that its phases are in the engine's convention.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Constituent:
    name: str
    speed: float  # deg/h
    v0u: tuple[float, ...]  # deg, one per year
    f: tuple[float, ...]  # one per year


class TcdTable:
    """The TCD dump: constituents by TCD name, the year range and the TCD SHA-256."""

    def __init__(self, data: Mapping) -> None:
        self.first_year, self.last_year = (int(y) for y in data["years"])
        if self.last_year < self.first_year:
            raise ValueError(f"bad year range {data['years']}")
        n = self.last_year - self.first_year + 1
        self.tcd_sha256: str = data["tcd_sha256"]
        self.aliases: Mapping[str, str] = MappingProxyType(dict(data["aliases"]))
        consts = {}
        for name, c in data["constituents"].items():
            if len(c["v0u"]) != n or len(c["f"]) != n:
                raise ValueError(f"{name}: expected {n} yearly values")
            consts[name] = Constituent(name, float(c["speed"]), tuple(c["v0u"]), tuple(c["f"]))
        self.constituents: Mapping[str, Constituent] = MappingProxyType(consts)

    @classmethod
    def load(cls, path: str | Path, tcd_sha256: str | None = None) -> "TcdTable":
        """Read a dump. With tcd_sha256, raise unless the dump was made from that TCD."""
        table = cls(json.loads(Path(path).read_text()))
        if tcd_sha256 is not None and table.tcd_sha256 != tcd_sha256:
            raise ValueError(f"dump is for TCD {table.tcd_sha256}, expected {tcd_sha256}")
        return table

    def name(self, raw: str) -> str:
        """TCD name for a constituent name (TICON spellings mapped). KeyError if unknown."""
        upcased = raw.strip().upper()
        name = self.aliases.get(upcased, upcased)
        if name not in self.constituents:
            raise KeyError(f"constituent {raw!r} has no TCD definition")
        return name

    def __getitem__(self, raw: str) -> Constituent:
        return self.constituents[self.name(raw)]

    def __contains__(self, raw: object) -> bool:
        try:
            self.name(raw)  # type: ignore[arg-type]
        except (KeyError, AttributeError):
            return False
        return True

    def _index(self, year: int) -> int:
        if not self.first_year <= year <= self.last_year:
            raise ValueError(f"year {year} outside the TCD dump {self.first_year}-{self.last_year}")
        return year - self.first_year

    def speed(self, raw: str) -> float:
        return self[raw].speed

    def v0u(self, raw: str, year: int) -> float:
        return self[raw].v0u[self._index(year)]

    def f(self, raw: str, year: int) -> float:
        return self[raw].f[self._index(year)]

