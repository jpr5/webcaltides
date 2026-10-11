"""TEST ONLY: an astro table straight from the TCD dump, so that a synthetic test can build a whole
valid format 1.0 document from a small made-up dump.

The release's per-year tables are KP0b's: otc_pipeline.astro_tables.astro_table, from a congen run
(scripts/otc/tools/congen_tables.sh, 1700-2100) checked equal to the TCD. Nothing in otc_pipeline
imports this module, and a release never uses it.
"""
from __future__ import annotations

from typing import Iterable

from gesla.tcd_table import TcdTable


def astro_table(table: TcdTable, names: Iterable[str], *, table_id: str, first_year: int, last_year: int) -> dict:
    rows = {}
    for n in sorted(set(names)):
        c = table[n]
        years = range(first_year, last_year + 1)
        v0u = [round(table.v0u(n, y) % 360.0, 2) % 360.0 for y in years]
        rows[n] = {"speed_deg_per_hour": c.speed, "v0u_deg": v0u, "f": [round(table.f(n, y), 4) for y in years]}
    return {"astro_table_id": table_id, "v0_model": "schureman_tcd", "tables_sha256": table.tcd_sha256,
            "generator": "TEST ONLY: the TCD dump's V0+u and f (the release's tables are KP0b's)",
            "first_year": first_year, "last_year": last_year, "constituents": rows}
