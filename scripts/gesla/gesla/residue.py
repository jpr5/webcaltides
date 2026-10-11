"""Residue rows: the cases tier 1 cannot settle, for the decision model (BP §7.1; manifest §2.2).

Every producer writes ResidueCase rows to $OTC_WORK/builds/<YYYYMMDD>/gesla/residue.jsonl (a
build output: never committed; uploaded to builds/<YYYYMMDD>/ in the work bucket). G10b reads it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CASES = ("timebase", "k5", "sibling", "g6_tie", "g6_zero", "g7_events", "g8_step", "g11_shift", "tz", "d9")


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _check_numbers(obj, where: str) -> None:
    """Evidence holds structured data: numbers, short labels, lists and dicts of them. A
    non-finite float would make the canonical JSON (and so the SHA-256) undefined."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError(f"{where}: evidence key {k!r} is not a string")
            _check_numbers(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_numbers(v, f"{where}[{i}]")
    elif isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{where}: non-finite number {obj!r} (use None)")
    elif obj is not None and not isinstance(obj, (int, str, bool)):
        raise ValueError(f"{where}: {type(obj).__name__} is not structured evidence")


@dataclass(frozen=True)
class ResidueCase:
    subject_id: str     # station id or record id
    case: str           # one of CASES
    rule: str           # tier-1 rule that sent it, e.g. "D5"
    reason: str
    evidence: dict = field(default_factory=dict)   # structured numbers only (BP §7.1 tier-2 list)

    def __post_init__(self) -> None:
        if self.case not in CASES:
            raise ValueError(f"{self.subject_id}: unknown residue case {self.case!r}")
        if not self.subject_id or not self.rule or not self.reason:
            raise ValueError(f"residue case needs subject_id, rule and reason: {self!r}")
        _check_numbers(self.evidence, self.subject_id)

    def evidence_sha256(self) -> str:
        """SHA-256 of the evidence as canonical JSON (sorted keys, no spaces)."""
        return hashlib.sha256(_canonical(self.evidence).encode()).hexdigest()

    def to_json(self) -> str:
        return _canonical({"subject_id": self.subject_id, "case": self.case, "rule": self.rule,
                           "reason": self.reason, "evidence": self.evidence,
                           "evidence_sha256": self.evidence_sha256()})

    @classmethod
    def from_json(cls, line: str) -> "ResidueCase":
        d = json.loads(line)
        rc = cls(d["subject_id"], d["case"], d["rule"], d["reason"], d.get("evidence", {}))
        if "evidence_sha256" not in d:
            raise ValueError(f"{rc.subject_id}: no evidence_sha256")
        if d["evidence_sha256"] != rc.evidence_sha256():
            raise ValueError(f"{rc.subject_id}: evidence_sha256 does not match the evidence")
        return rc


def residue_path(work: Path, build_date: dt.date | str) -> Path:
    stamp = build_date if isinstance(build_date, str) else build_date.strftime("%Y%m%d")
    return Path(work) / "builds" / stamp / "gesla" / "residue.jsonl"


def append(path: Path, cases: Iterable[ResidueCase]) -> int:
    """Append cases (one JSON object per line). Returns how many were written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for c in cases:
            fh.write(c.to_json() + "\n")
            n += 1
    return n


def replace_case(path: Path, case: str, cases: Iterable[ResidueCase]) -> int:
    """Replace every row of `case` with `cases` (other producers' rows kept, order kept), written
    atomically: a producer re-run for the same build never duplicates its rows. Returns how many
    were written."""
    path = Path(path)
    cases = list(cases)
    if any(c.case != case for c in cases):
        raise ValueError(f"replace_case({case!r}): a row of another case")
    keep = []
    if path.exists():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                other = json.loads(line)["case"] != case
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(f"{path}:{n}: {e}") from e
            if other:
                keep.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(x + "\n" for x in keep + [c.to_json() for c in cases]), encoding="utf-8")
    os.replace(tmp, path)
    return len(cases)


def read(path: Path) -> list[ResidueCase]:
    path = Path(path)
    if not path.exists():
        return []
    return [ResidueCase.from_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["CASES", "ResidueCase", "residue_path", "append", "replace_case", "read"]
