"""Published datum levels, as the official adapters write them (datums spec §4.1, slots DT5a-DT5e).

A published level is the publisher's number, converted to metres and re-expressed above the set's
zero, with a `basis` of kind `published`, method `source`, the publisher's own name for it and,
only where the publisher states one, its epoch and control station. Nothing here computes a level.

`datum_build_errors` runs the release build checks the schema cannot express that touch a single
datum object: check 8 (chart_datum names a key of named), check 9 (basis and named have the same
keys) and check 13 (every datum date is a real calendar date; every period has start <= end).
"""
from __future__ import annotations

from datetime import date

__all__ = ["datum_build_errors", "period", "published"]


def period(start: str, end: str, name: str | None = None) -> dict:
    """A `datum_period` (inclusive dates YYYY-MM-DD). Raises ValueError on an impossible date or
    an end before the start, so a bad epoch never reaches a release."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if e < s:
        raise ValueError(f"period {start}..{end}: end before start")
    out = {"start": s.isoformat(), "end": e.isoformat()}
    if name:
        out["name"] = name
    return out


def published(source_name: str, *, epoch: dict | None = None, control_source_id: str | None = None,
              provisional: bool = False) -> dict:
    """The basis of one published level: kind published, method source, the publisher's name."""
    if not source_name or len(source_name) > 31:
        raise ValueError(f"source_name {source_name!r} must be 1 to 31 characters")
    b = {"kind": "published", "method": "source", "source_name": source_name}
    if epoch is not None:
        b["epoch"] = epoch
    if control_source_id:
        b["control"] = {"source_id": control_source_id}
    if provisional:
        b["flags"] = ["provisional"]
    return b


def _real_date(value) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def datum_build_errors(datum: dict, where: str = "datum") -> list[str]:
    """Build checks 8, 9 and 13 on one datum object (empty list = it passes)."""
    errors = []
    named, basis = datum.get("named"), datum.get("basis")
    cd = datum.get("chart_datum")
    if cd is not None and (not isinstance(named, dict) or cd not in named):
        errors.append(f"{where}: check 8: chart_datum {cd!r} is not a key of named")
    if isinstance(named, dict) or isinstance(basis, dict):
        nk, bk = set(named or {}), set(basis or {})
        if nk != bk:
            errors.append(f"{where}: check 9: named and basis keys differ: only in named {sorted(nk - bk)}, "
                          f"only in basis {sorted(bk - nk)}")
    for key, b in sorted((basis or {}).items()):
        for field in ("epoch", "data_span"):
            p = b.get(field) if isinstance(b, dict) else None
            if not isinstance(p, dict):
                continue
            s, e = p.get("start"), p.get("end")
            if not (_real_date(s) and _real_date(e)):
                errors.append(f"{where}: check 13: {key}.{field} {s!r}..{e!r} is not a real date pair")
            elif e < s:
                errors.append(f"{where}: check 13: {key}.{field} ends before it starts")
    return errors
