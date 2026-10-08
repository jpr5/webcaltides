"""GaugeRecord: one gauge's observed series, as the source stamped it (GATE-REC).

This is the contract between the GESLA reader (read_gesla.py) and every gauge adapter
(PEGELONLINE, RWS, Marine Institute, DMI, SMHI, FMI, UHSLC fast delivery, SHOM REFMAR).
QC, the time-base audit and the fit read only this type. The fields are frozen: a change
needs the orchestrator and every adapter slot told (manifest §2.1).

The constructor checks the invariants every consumer relies on and makes the three arrays
read-only, so a record cannot change after it is built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import numpy as np

SOURCES = frozenset({
    "gesla", "pegelonline", "rws", "marine-institute", "dmi", "smhi", "fmi", "uhslc-fd", "shom-refmar",
})
SAMPLING = frozenset({"instantaneous", "mean"})
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class GaugeRecord:
    record_id: str          # GESLA file name, or "<source>:<upstream station id>"
    source: str             # "gesla", "pegelonline", "rws", "marine-institute", "dmi", "smhi", "fmi", "uhslc-fd", "shom-refmar"
    source_version: str     # GESLA "4.1"; other sources: archive datestamp YYYYMMDD
    contributor: str        # GESLA contributor abbreviation, or the agency
    originator: bool        # True when the agency operates the gauge (BP §4 rule 3)
    licence_class: str      # GESLA licence-page class, or the source's SPDX id
    lat: float
    lon: float
    gauge_type: str         # GESLA "GAUGE TYPE" value, e.g. "Coastal", "River", "Lake"
    sampling: Literal["instantaneous", "mean"]
    interval_min: int
    declared_time_base: str | None   # adapter's declaration (BP §2 class name) or None for GESLA
    member_sha256: str      # SHA-256 of the raw member/file the series came from
    times_s: np.ndarray     # int64 Unix seconds, AS STAMPED (no correction applied)
    heights_m: np.ndarray   # float64 metres
    good: np.ndarray        # bool: use-flag 1 and QC flag in {0,1}

    def __post_init__(self) -> None:
        errors = []
        if not self.record_id:
            errors.append("record_id is empty")
        if self.source not in SOURCES:
            errors.append(f"source {self.source!r} is not one of {sorted(SOURCES)}")
        elif self.source != "gesla" and not self.record_id.startswith(f"{self.source}:"):
            errors.append(f"record_id {self.record_id!r} must start with '{self.source}:'")
        if not self.source_version:
            errors.append("source_version is empty")
        if not self.contributor:
            errors.append("contributor is empty")
        if not isinstance(self.originator, bool):
            errors.append("originator must be a bool")
        if not self.licence_class:
            errors.append("licence_class is empty")
        if not (-90.0 <= self.lat <= 90.0):
            errors.append(f"lat {self.lat} outside [-90, 90]")
        if not (-180.0 <= self.lon <= 360.0):
            errors.append(f"lon {self.lon} outside [-180, 360]")
        if not self.gauge_type:
            errors.append("gauge_type is empty")
        if self.sampling not in SAMPLING:
            errors.append(f"sampling {self.sampling!r} is not one of {sorted(SAMPLING)}")
        if isinstance(self.interval_min, bool) or not isinstance(self.interval_min, int) or self.interval_min <= 0:
            errors.append(f"interval_min {self.interval_min!r} must be a positive int")
        if self.source == "gesla" and self.declared_time_base is not None:
            errors.append("declared_time_base must be None for GESLA (the audit decides)")
        if not _SHA256.match(self.member_sha256 or ""):
            errors.append(f"member_sha256 {self.member_sha256!r} is not a lower-case SHA-256")
        for name, dtype in (("times_s", np.int64), ("heights_m", np.float64), ("good", np.bool_)):
            arr = getattr(self, name)
            if not isinstance(arr, np.ndarray) or arr.ndim != 1 or arr.dtype != dtype:
                errors.append(f"{name} must be a 1-D {np.dtype(dtype).name} array")
        if not errors and not (len(self.times_s) == len(self.heights_m) == len(self.good)):
            errors.append("times_s, heights_m and good differ in length")
        if not errors and np.any(self.good & ~np.isfinite(self.heights_m)):
            errors.append("a good sample has a non-finite height")
        if errors:
            raise ValueError(f"GaugeRecord {self.record_id!r}: " + "; ".join(errors))
        for name in ("times_s", "heights_m", "good"):
            getattr(self, name).setflags(write=False)

    def __len__(self) -> int:
        return len(self.times_s)
