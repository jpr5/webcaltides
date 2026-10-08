"""QC before the fit (BP §5), on a GaugeRecord.

Order (BP §5):
1. drop null heights;
2. drop samples with use flag 0 or a QC flag outside {0, 1};
   (the reader folds 1 and 2 into GaugeRecord.good)
3. remove duplicate stamps (the first in file order is kept);
4. for sub-hourly records, keep one instantaneous value per hour on a fixed minute
   (the record's modal minute; ties go to the earlier minute);
5. apply the time-base correction (a hook; identity until G04b supplies the corrections);
6. spike removal, after a first fit: drop |residual - 25-h centred rolling median| >
   6 x 1.4826 x MAD, then refit (fit.fit_despiked runs this step).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .record import GaugeRecord

# (times_s, heights_m) -> (times_s, heights_m), times in true UTC afterwards.
TimeBaseCorrection = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]

SPIKE_K = 6.0
MAD_SCALE = 1.4826
SPIKE_WINDOW = "25h"
SPIKE_MIN_PERIODS = 6


def identity_correction(times_s: np.ndarray, heights_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return times_s, heights_m


@dataclass(frozen=True)
class Series:
    """A cleaned, sorted series with unique stamps: what the fit reads."""
    record_id: str
    times_s: np.ndarray     # int64 Unix seconds, strictly increasing
    heights_m: np.ndarray   # float64, finite
    stats: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.times_s.dtype != np.int64 or self.heights_m.dtype != np.float64:
            raise ValueError("Series wants int64 times and float64 heights")
        if len(self.times_s) != len(self.heights_m):
            raise ValueError("times and heights differ in length")
        if len(self.times_s) > 1 and np.any(np.diff(self.times_s) <= 0):
            raise ValueError("times are not strictly increasing")
        if not np.all(np.isfinite(self.heights_m)):
            raise ValueError("non-finite height in a cleaned series")

    def __len__(self) -> int:
        return len(self.times_s)


def clean(record: GaugeRecord, correction: TimeBaseCorrection = identity_correction) -> Series:
    """Steps 1-5 on a record. Step 6 needs a fit; see fit.fit_despiked."""
    n_rows = len(record)
    non_null = int(np.isfinite(record.heights_m).sum())
    t = record.times_s[record.good]
    h = record.heights_m[record.good]
    stats = {"rows": n_rows, "non_null": non_null, "flag_dropped": non_null - int(record.good.sum())}

    order = np.argsort(t, kind="stable")
    t, h = t[order], h[order]
    first = np.ones(len(t), dtype=bool)
    first[1:] = t[1:] != t[:-1]
    stats["duplicates_dropped"] = int((~first).sum())
    t, h = t[first], h[first]

    t, h = to_hourly(t, h)
    stats["hourly"] = len(t)

    t, h = correction(t, h)
    t = np.asarray(t, dtype=np.int64)
    h = np.asarray(h, dtype=np.float64)
    order = np.argsort(t, kind="stable")
    t, h = t[order], h[order]
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = t[1:] != t[:-1]
    if not keep.all():
        raise ValueError(f"{record.record_id}: the time-base correction made {int((~keep).sum())} stamps collide")
    return Series(record.record_id, t, h, stats)


def to_hourly(times_s: np.ndarray, heights_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sub-hourly (median step < 1 h): keep the samples on the modal minute of the hour."""
    if len(times_s) < 2 or np.median(np.diff(times_s)) >= 3600:
        return times_s, heights_m
    minutes = (times_s // 60) % 60
    counts = np.bincount(minutes, minlength=60)
    mode = int(np.argmax(counts))
    keep = minutes == mode
    return times_s[keep], heights_m[keep]


def despike_mask(times_s: np.ndarray, residual: np.ndarray, k: float = SPIKE_K) -> tuple[np.ndarray, float]:
    """True where a sample is kept: |r - 25-h centred rolling median of r| <= k * 1.4826 * MAD."""
    idx = pd.DatetimeIndex(times_s.astype("datetime64[s]"))
    r = pd.Series(residual, index=idx)
    lp = r.rolling(SPIKE_WINDOW, center=True, min_periods=SPIKE_MIN_PERIODS).median()
    d = (r - lp).to_numpy()
    mad = float(np.nanmedian(np.abs(d - np.nanmedian(d))) * MAD_SCALE)
    with np.errstate(invalid="ignore"):
        keep = np.abs(d) <= k * mad
    return keep, mad
