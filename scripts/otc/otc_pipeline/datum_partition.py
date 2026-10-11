"""The datums holdout partition rule (datums spec §5.1), sealed in slot DT0b, and the one loader of the
sealed lists.

h = SHA-256("otc-datums-holdout-v1|" + source + "|" + id) over the UTF-8 bytes; the partition is
the first 8 bytes of h, read as an unsigned big-endian integer, mod 3. 0 = tuning, 1 = holdout,
2 = reserve. Ids: `noaa` the 7-digit station id, `kartverket` the station code, `linz` the
standard port's name in lower case with spaces as `_`, `pegelonline` the station uuid in lower case.
The rule refuses an unknown source or an id not already in that form: it never normalises.

After the seal, tuning code may use the tuning partition only. Every reader of the sealed files goes
through `load`, which returns one partition by its explicit name and checks the file's sealed SHA-256.
"""
import hashlib
import json
import re
from pathlib import Path

PARTITIONS = ("tuning", "holdout", "reserve")
SOURCES = ("noaa", "kartverket", "linz", "pegelonline")
_ID = {
    "noaa": re.compile(r"[0-9]{7}"),
    "kartverket": re.compile(r"[A-Z]{3}"),
    "linz": re.compile(r"[a-z]+(?:_[a-z]+)*"),
    "pegelonline": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
}


def partition_key(source: str, id: str) -> int:
    """The first 8 bytes of h as an unsigned big-endian integer (also the T1 list order)."""
    if source not in _ID:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    if not isinstance(id, str) or not _ID[source].fullmatch(id):
        raise ValueError(f"{source} id {id!r} is not in canonical form")
    h = hashlib.sha256(f"otc-datums-holdout-v1|{source}|{id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def partition(source: str, id: str) -> str:
    return PARTITIONS[partition_key(source, id) % 3]


# --- the sealed lists ----------------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "datums"
SEALED = {   # file name -> SHA-256 recorded in the slot report when the file was sealed
    "partitions.json": "7c3ab369bbf6884549e795c2e538f965eb886634dfb17e397755a926830659ee",
    "t1_stations.json": "6d71c4a2a894983e11a7bed20707c0371c2e28aa28c6288bf51f98855780b2f2",
}


def _sealed(name: str, data_dir: Path | None) -> dict:
    raw = (Path(data_dir) if data_dir else DATA_DIR).joinpath(name).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != SEALED[name]:
        raise ValueError(f"{name}: SHA-256 {got} is not the sealed {SEALED[name]}")
    return json.loads(raw)


def load(kind: str, partition: str, source: str | None = None, *, sealed_run: bool = False,
         data_dir: Path | None = None) -> list:
    """One sealed partition, by explicit name.

    kind "partitions": the ids of `source` in `partition`. kind "t1": the T1 stations of `partition`
    (NOAA; dicts with id, class, key_hex), in sealed order. "holdout" and "reserve" are refused unless
    sealed_run=True, which only the sealed runs (DT3 run 1 and run 2) and the seal's own checks pass.
    """
    if not isinstance(partition, str) or partition not in PARTITIONS:
        raise ValueError(f"partition must be one of {PARTITIONS}, got {partition!r}")
    if kind == "partitions":
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    elif kind == "t1":
        if source is not None:
            raise ValueError("the T1 lists are NOAA only; pass no source")
    else:
        raise ValueError(f"kind must be 'partitions' or 't1', got {kind!r}")
    if partition != "tuning" and not sealed_run:
        raise PermissionError(f"the {partition} partition is sealed: only a sealed run may load it")
    if kind == "partitions":
        return list(_sealed("partitions.json", data_dir)["partitions"][source][partition])
    return [dict(s) for s in _sealed("t1_stations.json", data_dir)["partitions"][partition]["stations"]]
