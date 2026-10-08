"""Read GESLA-4 records into GaugeRecord.

The GESLA release lives in the private work bucket; scripts/gesla/fetch_mirror.rb copies it
into the local cache ($OTC_WORK/inputs/gesla/<version>/) and verifies it. This module reads
from that cache and checks again, so a record is never built from bytes that do not match
the pinned manifest:

1. MANIFEST.json is checked against the SHA-256 pinned in data/gesla/inputs.lock.json.
2. The metadata CSV is checked against its manifest entry before it is parsed.
3. Each member is read from the zip and its SHA-256 compared with the manifest's entry.

A GESLA-4 file is a '#' header followed by rows "yyyy/mm/dd hh:mm:ss height qc use".
The series is kept AS STAMPED (no time-zone or time-base correction; that is the time-base
audit's job). Null heights (the header's NULL VALUE, or non-finite) become NaN and are not
good. good = use flag 1 and QC flag in {0, 1}, and a real height.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .record import GaugeRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / "data" / "gesla" / "inputs.lock.json"
DEFAULT_VERSION = "4.1"


def metadata_path(version: str) -> str:
    """The metadata CSV's path in the release: GESLA 4.1 -> metadata/GESLA4-1_ALL.csv."""
    return f"metadata/GESLA{version.replace('.', '-')}_ALL.csv"


METADATA_PATH = metadata_path(DEFAULT_VERSION)
UNLISTED = "unlisted"


class ShaMismatch(ValueError):
    """A file or member does not match the SHA-256 the manifest (or the pin) records."""


def otc_work() -> Path:
    return Path(os.environ.get("OTC_WORK", "~/.local/share/opentideconstants/work")).expanduser()


def gesla_dir(version: str = DEFAULT_VERSION) -> Path:
    return otc_work() / "inputs" / "gesla" / version


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ContributorRole:
    originator: bool
    licence_class: str


def load_contributor_roles(path: str | Path) -> dict[str, ContributorRole]:
    """data/gesla/contributor_roles.csv (G02): contributor, role (originator|redistributor), licence_class."""
    roles = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("contributor") or "").strip()
            if row.get("role") is None or row.get("licence_class") is None:
                raise ValueError(f"{path}: contributor {name!r}: the row is short (needs role and licence_class)")
            role = row["role"].strip()
            if role not in ("originator", "redistributor", UNLISTED):
                raise ValueError(f"{path}: contributor {name!r} has unknown role {role!r}")
            if name in roles:
                raise ValueError(f"{path}: contributor {name!r} is listed twice")
            roles[name] = ContributorRole(role == "originator", row["licence_class"].strip())
    return roles


def role_for(contributor: str, roles: Mapping[str, ContributorRole]) -> ContributorRole:
    """A contributor missing from the roles table is 'unlisted' and not an originator (G02 rule)."""
    return roles.get(contributor, ContributorRole(False, UNLISTED))


class GeslaRelease:
    """A verified GESLA release in the local cache: manifest, metadata and zip members."""

    def __init__(self, root: str | Path | None = None, *, version: str = DEFAULT_VERSION,
                 lock_path: str | Path | None = LOCK_PATH) -> None:
        self.root = Path(root) if root is not None else gesla_dir(version)
        self.version = version
        manifest_path = self.root / "MANIFEST.json"
        raw = manifest_path.read_bytes()
        if lock_path is not None:
            key = f"inputs/gesla/{version}/MANIFEST.json"
            pin = json.loads(Path(lock_path).read_text()).get("objects", {}).get(key)
            if pin is None:
                raise ValueError(f"{lock_path} has no pin for {key}")
            got = sha256_bytes(raw)
            if got != pin["sha256"] or len(raw) != pin["size"]:
                raise ShaMismatch(f"{manifest_path}: sha256 {got} size {len(raw)}, "
                                  f"lock pins {pin['sha256']} size {pin['size']}")
        self.manifest = json.loads(raw)
        if str(self.manifest.get("version")) != version:
            raise ValueError(f"{manifest_path}: version {self.manifest.get('version')!r}, expected {version!r}")
        self.members: dict[str, dict] = {m["name"]: m for m in self.manifest["members"]}
        self.files: dict[str, dict] = {f["path"]: f for f in self.manifest["files"]}
        self.metadata_path = metadata_path(version)
        self.zip_path = self.root / self.manifest["archive"]["name"]
        self._metadata: dict[str, dict[str, str]] | None = None

    # --- metadata ------------------------------------------------------------------------------

    def metadata(self) -> dict[str, dict[str, str]]:
        """Rows of the metadata CSV by file name, after checking the CSV against the manifest."""
        if self._metadata is None:
            path = self.root / self.metadata_path
            entry = self.files.get(self.metadata_path)
            if entry is None:
                raise ValueError(f"{self.root / 'MANIFEST.json'} has no entry for {self.metadata_path}")
            data = path.read_bytes()
            got = sha256_bytes(data)
            if got != entry["sha256"] or len(data) != entry["size"]:
                raise ShaMismatch(f"{path}: sha256 {got} size {len(data)}, "
                                  f"manifest has {entry['sha256']} size {entry['size']}")
            self._metadata = parse_metadata(data)
        return self._metadata

    # --- members -------------------------------------------------------------------------------

    def read_member(self, name: str) -> bytes:
        """The raw bytes of one record, checked against the manifest's size and SHA-256."""
        if name not in self.members:
            raise KeyError(f"{name!r} is not a member of GESLA {self.version}")
        entry = self.members[name]
        with zipfile.ZipFile(self.zip_path) as z:
            data = z.read(name)
        got = sha256_bytes(data)
        if got != entry["sha256"] or len(data) != entry["size"]:
            raise ShaMismatch(f"member {name}: sha256 {got} size {len(data)}, "
                              f"manifest has {entry['sha256']} size {entry['size']}")
        return data

    def record(self, name: str, roles: Mapping[str, ContributorRole]) -> GaugeRecord:
        meta = self.metadata().get(name)
        if meta is None:
            raise KeyError(f"{name!r} has no row in {self.metadata_path}")
        return parse_record(name, self.read_member(name), meta, roles,
                            source_version=self.version, expected_sha256=self.members[name]["sha256"])


def parse_metadata(data: bytes) -> dict[str, dict[str, str]]:
    """GESLA4-1_ALL.csv: CR line endings, and the header joins the last two names without a comma
    ("OVERALL RECORD QUALITYDOWNLOAD LINK"); the rows have the full 25 fields."""
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("metadata CSV is empty")
    header = [h.strip() for h in rows[0]]
    if header[-1] == "OVERALL RECORD QUALITYDOWNLOAD LINK":
        header = header[:-1] + ["OVERALL RECORD QUALITY", "DOWNLOAD LINK"]
    out = {}
    for row in rows[1:]:
        if not row:
            continue
        if len(row) != len(header):
            raise ValueError(f"metadata row {row[:1]} has {len(row)} fields, header has {len(header)}")
        rec = dict(zip(header, (v.strip() for v in row)))
        if rec["FILE NAME"] in out:
            raise ValueError(f"metadata lists {rec['FILE NAME']} twice")
        out[rec["FILE NAME"]] = rec
    return out


def parse_header(data: bytes) -> dict[str, str]:
    """The '# KEY value' header lines (up to the first data line), keyed by the GESLA field name."""
    keys = ("FORMAT VERSION", "SITE NAME", "SITE CODE", "COUNTRY", "CONTRIBUTOR", "LATITUDE", "LONGITUDE",
            "START DATE/TIME", "END DATE/TIME", "TIME ZONE HOURS", "DATUM INFORMATION", "NULL VALUE",
            "GAUGE TYPE", "OVERALL RECORD QUALITY")
    out: dict[str, str] = {}
    for raw in io.BytesIO(data):
        if not raw.startswith(b"#"):
            break
        line = raw[1:].decode("utf-8").strip()
        for k in keys:
            if line.startswith(k + " ") and k not in out:
                out[k] = line[len(k):].strip()
                break
    return out


def modal_interval_min(times_s: np.ndarray) -> int:
    """The most common positive step between stamps (ties: the shorter step), rounded to whole
    minutes (half up) and at least 1. A sub-minute or odd step (15 s, 90 s) is described, not
    rejected: QC (qc.hourly_mask) works on the stamps themselves."""
    steps = np.diff(np.unique(times_s))
    if len(steps) == 0:
        raise ValueError("fewer than two distinct stamps")
    vals, counts = np.unique(steps, return_counts=True)
    step = int(vals[np.argmax(counts)])
    return max(1, (step + 30) // 60)


def _interval(name: str, times_s: np.ndarray) -> int:
    try:
        return modal_interval_min(times_s)
    except ValueError as e:
        raise ValueError(f"{name}: {e}") from e


def parse_record(name: str, data: bytes, meta: Mapping[str, str], roles: Mapping[str, ContributorRole], *,
                 source_version: str, expected_sha256: str) -> GaugeRecord:
    """One GESLA-4 file (its raw bytes) -> GaugeRecord. expected_sha256 is the manifest's value."""
    sha = sha256_bytes(data)
    if sha != expected_sha256:
        raise ShaMismatch(f"member {name}: sha256 {sha}, manifest has {expected_sha256}")
    try:
        head = parse_header(data)
    except UnicodeDecodeError as e:
        raise ValueError(f"{name}: header is not UTF-8 ({e})") from e
    for k in ("LATITUDE", "LONGITUDE", "NULL VALUE", "GAUGE TYPE"):
        if k not in head:
            raise ValueError(f"{name}: header lacks {k}")
    for k, mk in (("GAUGE TYPE", "GAUGE TYPE"), ("NULL VALUE", "NULL VALUE")):
        if head[k] != meta[mk]:
            raise ValueError(f"{name}: header {k} {head[k]!r} differs from metadata {meta[mk]!r}")
    null_value = float(head["NULL VALUE"])

    df = pd.read_csv(io.BytesIO(data), comment="#", sep=r"\s+", header=None,
                     names=["d", "t", "h", "qc", "use"], dtype={"d": str, "t": str, "h": np.float64,
                                                               "qc": np.int64, "use": np.int64}, engine="c")
    stamps = pd.to_datetime(df["d"] + " " + df["t"], format="%Y/%m/%d %H:%M:%S")
    times_s = (stamps.to_numpy(dtype="datetime64[s]").astype(np.int64))
    heights = df["h"].to_numpy(dtype=np.float64, copy=True)
    null = ~np.isfinite(heights) | np.isclose(heights, null_value, rtol=0.0, atol=5e-5)
    heights[null] = np.nan
    good = (~null) & (df["use"].to_numpy() == 1) & np.isin(df["qc"].to_numpy(), (0, 1))

    contributor = meta["CONTRIBUTOR (ABBREVIATED)"]
    role = role_for(contributor, roles)
    return GaugeRecord(
        record_id=name,
        source="gesla",
        source_version=source_version,
        contributor=contributor,
        originator=role.originator,
        licence_class=role.licence_class,
        lat=float(head["LATITUDE"]),
        lon=float(head["LONGITUDE"]),
        gauge_type=head["GAUGE TYPE"],
        sampling="instantaneous",   # GESLA declares no sampling; the time-base audit (G04) decides
        interval_min=_interval(name, times_s),
        declared_time_base=None,
        member_sha256=sha,
        times_s=times_s,
        heights_m=heights,
        good=np.asarray(good, dtype=np.bool_),
    )
