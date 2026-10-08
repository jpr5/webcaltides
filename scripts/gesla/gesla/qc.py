"""QC before the fit (BP §5), on a GaugeRecord.

Order (BP §5):
1. drop null heights;
2. drop samples with use flag 0 or a QC flag outside {0, 1};
   (the reader folds 1 and 2 into GaugeRecord.good)
3. remove duplicate stamps (the first in file order is kept);
4. keep one instantaneous value per hour: hours with one sample keep it; sub-hourly hours
   keep the sample on the record's modal offset in the hour (minute and second; ties go to
   the earlier offset) and are dropped, and counted, when no sample is on that offset;
5. apply the time-base correction (a hook; identity until G04b supplies the corrections);
6. spike removal, after a first fit: drop |residual - 25-h centred rolling median| >
   6 x 1.4826 x MAD, then refit (fit.fit_despiked runs this step). Samples with fewer than
   6 samples in their window cannot be assessed and are kept; a MAD of 0 or NaN drops nothing.
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

    keep, stats["hours_off_modal_dropped"] = hourly_mask(t)
    t, h = t[keep], h[keep]
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


def hourly_mask(times_s: np.ndarray) -> tuple[np.ndarray, int]:
    """Step 4 on sorted, unique stamps. Returns (keep, hours_dropped).

    An hour (floor(t / 3600)) with one sample keeps it: that part of the record is hourly
    (or coarser), whatever its minute. An hour with several samples is sub-hourly: it keeps
    only the sample on the record's modal offset within the hour (minute and second, taken
    over the sub-hourly hours; ties go to the earlier offset). A sub-hourly hour with no
    sample on that offset is dropped and counted.
    """
    t = np.asarray(times_s, dtype=np.int64)
    keep = np.ones(len(t), dtype=bool)
    if len(t) < 2:
        return keep, 0
    hour = t // 3600
    _, start, counts = np.unique(hour, return_index=True, return_counts=True)
    multi = np.repeat(counts > 1, counts)
    if not multi.any():
        return keep, 0
    offset = t % 3600
    mode = int(np.argmax(np.bincount(offset[multi], minlength=3600)))
    keep = ~multi | (offset == mode)
    multi_hours = hour[multi]
    kept_hours = hour[multi & keep]
    return keep, int(len(np.unique(multi_hours)) - len(np.unique(kept_hours)))


def to_hourly(times_s: np.ndarray, heights_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One instantaneous value per hour (see hourly_mask)."""
    keep, _ = hourly_mask(times_s)
    return times_s[keep], heights_m[keep]


def despike_mask(times_s: np.ndarray, residual: np.ndarray, k: float = SPIKE_K, *,
                 keep_unassessed: bool = True) -> tuple[np.ndarray, float]:
    """True where a sample is kept: |r - 25-h centred rolling median of r| <= k * 1.4826 * MAD.

    A sample whose 25-h window holds fewer than SPIKE_MIN_PERIODS samples cannot be assessed
    and is kept (despike_stats counts it). When no sample can be assessed the MAD is NaN, and
    when the MAD is 0 (a quantised or flat residual) no threshold exists: in both cases no
    sample is dropped. keep_unassessed=False reproduces the PoC, which dropped unassessable
    samples with the spikes; it exists for the PoC comparison only.
    """
    d = _spike_distance(times_s, residual)
    assessed = np.isfinite(d)
    if not assessed.any():
        return (np.ones(len(d), dtype=bool) if keep_unassessed else assessed), float("nan")
    da = d[assessed]
    mad = float(np.median(np.abs(da - np.median(da))) * MAD_SCALE)
    keep = np.ones(len(d), dtype=bool) if keep_unassessed else assessed.copy()
    if mad > 0:
        keep[assessed] = np.abs(da) <= k * mad
    return keep, mad


def unassessed_count(times_s: np.ndarray, residual: np.ndarray) -> int:
    """How many samples have too few neighbours in their 25-h window to be assessed for spikes."""
    return int((~np.isfinite(_spike_distance(times_s, residual))).sum())


def _spike_distance(times_s: np.ndarray, residual: np.ndarray) -> np.ndarray:
    idx = pd.DatetimeIndex(np.asarray(times_s, dtype=np.int64).astype("datetime64[s]"))
    r = pd.Series(residual, index=idx)
    lp = r.rolling(SPIKE_WINDOW, center=True, min_periods=SPIKE_MIN_PERIODS).median()
    return (r - lp).to_numpy()
