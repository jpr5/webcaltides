"""Load the two sealed NOAA 2020 Table 2 profiles without live requests or fits.

This loader retains source parameters and evidence. It does not establish current
validity, allocate station identities, select references or publish predictions.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import re

from timezonefinder import TimezoneFinder
from otc_pipeline import station_map as sm


_MANIFEST = "noaa-two-gap-followup-v1-manifest.json"
_HISTORY = "evidence/official-prediction-update-history.response"
_NOTICE = "evidence/noaa-correct-disclaimers.response"
# Independent primary-input acceptance, not a prediction/accuracy acceptance.
_ACCEPTANCE_SHA256 = "4b618a4ec3de7854b29f6ead6622a3c16050fd441724b569ca7bfb66a59dceda"
_SPECS = (
    ("atlantic", 2513, 345, 351, "TEC2869", "Lockwoods Folly Inlet", "8661070", "OTC-a6c00a3f", "R", 4, 15, 0.84, 1.0, "75°W", -5),
    ("pacific", 517, 284, 302, "TWC0509", "San Francisco Bar", "9414290", "OTC-ee4593cd", "A", -35, -31, -0.06096, 0.0, "120°W", -8),
)
_ROW = re.compile(
    r"^\s*(\d+)\s+(.+?)\s+(\d+)°\s+(\d+)'\s+(\d+)°\s+(\d+)'\s+"
    r"([+-]\d+\s+\d{2})\s+([+-]\d+\s+\d{2})\s+"
    r"(\*?[+-]?\d+\.\d+)\s+(\*?[+-]?\d+\.\d+)\s+"
    r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s*$"
)


@dataclass(frozen=True)
class HistoricalInputs:
    """Complete verified extra inventory; artifacts are not additional places."""

    profiles: tuple[dict, ...]
    manifest_sha256: str
    members: tuple[dict, ...]
    input_roles: tuple[dict, ...]
    matrix_sha256: str
    manifest: dict
    place_parameter_rows: int = 2


def _read(bundle: Path, relative: str) -> bytes:
    path = PurePosixPath(relative)
    if (not relative or path.is_absolute() or ".." in path.parts
            or path.as_posix() != relative or "\\" in relative):
        raise ValueError(f"historical input path outside bundle: {relative!r}")
    try:
        resolved = (bundle / relative).resolve(strict=True)
        if not resolved.is_relative_to(bundle):
            raise ValueError(f"historical input path outside bundle: {relative!r}")
        return resolved.read_bytes()
    except OSError as error:
        raise ValueError(f"historical input missing/unreadable: {relative}") from error


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _role(path: str) -> str:
    if path.endswith(".provenance.json") or path.endswith(".meta.json"):
        return "acquisition_provenance"
    if path.endswith(".request.json"):
        return "acquisition_request"
    if path.startswith("derived/"):
        if path.endswith(".png"):
            return "visual_row_evidence"
        if "time-datum" in path:
            return "clock_datum_evidence"
        return "primary_row_extraction"
    if "annual-official-book.response" in path:
        return "published_parameter_book"
    if path == _HISTORY:
        return "source_lifecycle_evidence"
    if path == _NOTICE:
        return "rights_evidence"
    if "inherited-hilo" in path or "exact-current-metadata" in path or "exact-offsets" in path:
        return "unavailable_current_source_evidence"
    return "captured_interface_evidence"


def _minutes(value: str) -> int:
    hours, minutes = value.split()
    return (-1 if hours.startswith("-") else 1) * (abs(int(hours)) * 60 + int(minutes))


def _source_row(row: dict, spec: tuple, data: dict[str, bytes]) -> tuple[str, dict]:
    book, number, printed, pdf, legacy, name, reference, _, kind, high_t, low_t, high_h, low_h, meridian, utc = spec
    if (row["source"], row["source_version"], row["annual_table_row"], row["printed_page"], row["pdf_page"],
            row["legacy_source_record_id"], row["name"], row["candidate_reference_noaa_id"]) != (
            "noaa", "annual-tide-tables-2020", number, printed, pdf, legacy, name, reference):
        raise ValueError("historical source row/reference locator mismatch")
    false_fields = ("exact_legacy_id_in_annual_row", "modern_replacement_alias_verified",
                    "msl_offset_or_subordinate_datum_epoch_verified", "reference_model_2020_equivalence_verified",
                    "native_augmentation_or_prediction_validation_run", "source_2026_prediction_claim")
    if any(row.get(key) is not False for key in false_fields) or not row.get("id_crosswalk_status", "").startswith("inferred_"):
        raise ValueError("historical inferred crosswalk/current validity annotations missing or changed")
    if (row["nominal_position_precision"] != "one arcminute; not exact gauge survey coordinates"
            or row["chart_datum"] != "MLLW" or row["table_standard_meridian"] != meridian
            or row["table_fixed_utc_offset_hours"] != utc or row["daylight_saving_applied_in_book"] is not False):
        raise ValueError("historical position/clock/datum source annotation changed")
    body = f"evidence/2020-{book}-annual-official-book.response"
    if row["annual_body"] != body or not data[body].startswith(b"%PDF"):
        raise ValueError("historical primary book binding mismatch")
    page = data[f"derived/{book}-target-page-{pdf}.txt"].decode("utf-8")
    lines = [line for line in page.splitlines() if re.match(rf"^\s*{number}\s", line)]
    if len(lines) != 1:
        raise ValueError("historical raw row locator not unique")
    raw = lines[0]
    match = _ROW.fullmatch(raw)
    if match is None or re.sub(r"[\s.]+$", "", match[2]) != name:
        raise ValueError("historical raw row text does not match source identity")
    lat = int(match[3]) + int(match[4]) / 60
    lon = -(int(match[5]) + int(match[6]) / 60)
    raw_position = {"latitude": f"{match[3]}°{match[4]}′N", "longitude": f"{match[5]}°{match[6]}′W"}
    if row["raw_position"] != raw_position:
        raise ValueError("historical raw position differs from published minute precision")
    if row["nominal_position_degrees"] != {"latitude": lat, "longitude": lon}:
        raise ValueError("historical nominal coordinates differ from published minute precision")
    height_tokens = (match[9], match[10])
    starred = tuple(token.startswith("*") for token in height_tokens)
    if starred != ((True, True) if kind == "R" else (False, False)):
        raise ValueError("historical ratio/additive stars mismatch")
    factor = Decimal(1) if kind == "R" else Decimal("0.3048")
    numeric = {
        "height_adjusted_type": kind,
        "time_offset_high_min": _minutes(match[7]),
        "time_offset_low_min": _minutes(match[8]),
        "height_offset_high": float(Decimal(height_tokens[0].lstrip("*")) * factor),
        "height_offset_low": float(Decimal(height_tokens[1].lstrip("*")) * factor),
    }
    expected = dict(zip(numeric, (kind, high_t, low_t, high_h, low_h), strict=True))
    if numeric != expected or row["contract_numeric_representation"] != numeric:
        raise ValueError("historical ratio/additive conversion or published numeric values mismatch")
    raw_offsets: dict[str, str | float] = {"high_time": " ".join(match[7].split()), "low_time": " ".join(match[8].split())}
    if kind == "R":
        raw_offsets.update(high_height=height_tokens[0], low_height=height_tokens[1])
    else:
        raw_offsets.update(high_height_feet=float(height_tokens[0]), low_height_feet=float(height_tokens[1]))
    if row["raw_offsets"] != raw_offsets:
        raise ValueError("historical raw offsets differ from primary row")
    introduction = data[f"derived/{book}-time-datum-introduction.txt"].decode("utf-8")
    if "DAYLIGHT-SAVING TIME IS NOT USED" not in introduction or "Time meridian, " + meridian.replace("W", " W") not in page:
        raise ValueError("historical standard clock evidence missing")
    return raw, numeric


def _profile(row: dict, spec: tuple, data: dict[str, bytes], sha256: str, matrix_sha: str) -> dict:
    book, number, _, pdf, legacy, name, reference, reference_sid, *_ = spec
    raw, numeric = _source_row(row, spec, data)
    history = data[_HISTORY].decode("utf-8")
    events = []
    for event in row["removal_events"]:
        if event.get("timezone_not_declared_by_history") is not True or event["operation"] != "Removed":
            raise ValueError("historical removal source clock annotation missing")
        blocks = re.findall(r"<tr>.*?</tr>", history, re.DOTALL)
        text = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", block)).split()) for block in blocks]
        needle = f"{event['source_clock_timestamp']} {legacy} {event['name']} Removed"
        if not any(needle in block for block in text):
            raise ValueError("historical removal event not in actual history")
        events.append({key: event[key] for key in ("source_clock_timestamp", "name", "operation")} |
                      {"source_timezone": None, "evidence_sha256": _digest(data[_HISTORY])})
    if len(events) != (1 if book == "atlantic" else 2):
        raise ValueError("historical removal evidence incomplete")
    source_id = f"annual-tide-tables-2020/{book}/Table2/{number}"
    offset_id = f"noaa-2020-{book}-table2-{number}"
    body = row["annual_body"]
    inputs = list(dict.fromkeys([_digest(data[body]), _digest(data[_HISTORY]), matrix_sha,
                                _digest(data[f"derived/{book}-target-page-{pdf}.txt"]),
                                _digest(data[f"derived/{book}-time-datum-introduction.txt"]), _digest(data[_NOTICE])]))
    bindings = [_digest(data[body]), _digest(data[_HISTORY])]
    values = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    provenance = {
        "source_manifest_sha256": sha256, "input_sha256": inputs, "book": book,
        "annual_table_row": number, "printed_page": row["printed_page"], "pdf_page": pdf,
        "raw_row_text": raw, "raw_values_json": values,
        "id_crosswalk": {"legacy_id": legacy, "status": "inferred", "modern_alias_verified": False, "evidence_sha256": bindings},
        "reference_binding": {"noaa_id": reference, "station_id": reference_sid, "mapping_status": "primary_name_position", "evidence_sha256": bindings},
        "acceptance_report_sha256": _ACCEPTANCE_SHA256,
    }
    numeric.update({
        "reference_station_id": reference_sid, "licence_id": "noaa-public-domain",
        "datum": {"zero": "chart_datum", "chart_datum": "mllw", "named": {"mllw": 0.0},
                  "basis": {"mllw": {"kind": "published", "method": "source", "source_name": "MLLW"}}},
        "offset_id": offset_id, "source": "noaa", "source_type": "official",
        "source_version": "annual-tide-tables-2020", "source_record_id": source_id, "qc_status": "flagged",
        "qc_flags": [{"flag": "fallback:historical_offsets", "reason": "Official NOAA2020 profile retired in2026; current validity unestablished and accuracy unverified."}],
        "fallback": {"reason": "historical_offsets", "basis": "Actual NOAA2020 published Table2 source row",
                     "otc_finding": "Official profile retired in2026; current validity unestablished and current accuracy unverified."},
        "vintage": {"publication_year": 2020, "source_lifecycle": "retired", "removal_events": events,
                    "current_validity": "unestablished", "current_accuracy": "unverified", "reference_2020_equivalence": "not_checked"},
        "provenance": provenance,
    })
    return {
        "name": name, "lat": row["nominal_position_degrees"]["latitude"], "lon": row["nominal_position_degrees"]["longitude"],
        "subordinate_offsets": numeric,
        "source_records": [{"source": "noaa", "source_version": "annual-tide-tables-2020", "source_record_id": source_id,
                            "role": "reference", "disposition": "retained", "licence_id": "noaa-public-domain", "published_set_ids": [],
                            "detail": "Official2020 annual Table2 original; historical profile, not fitted constants or current-accuracy evidence.",
                            "provenance": {"input_sha256": inputs, "inventory_sha256": sha256},
                            "original_metadata": {"offset_id": offset_id, "raw_row_text": raw, "raw_values_json": values}}],
    }


def load_noaa_historical_inputs(bundle: Path, sha256: str) -> HistoricalInputs:
    """Verify every sealed member before returning either source-bound profile."""
    bundle = Path(bundle).resolve(strict=True)
    raw = _read(bundle, _MANIFEST)
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or _digest(raw) != sha256:
        raise ValueError("historical input manifest SHA-256 mismatch")
    manifest = json.loads(raw)
    members = manifest["members"]
    if manifest["format_version"] != 1 or len(members) != 56:
        raise ValueError("historical input manifest must retain all 56 members")
    data = {}
    for member in members:
        path = member["path"]
        if path in data:
            raise ValueError("historical input member path duplicated")
        content = _read(bundle, path)
        if _digest(content) != member["sha256"] or len(content) != member["bytes"]:
            raise ValueError(f"historical input member SHA-256/size mismatch: {path}")
        data[path] = content
    matrix = manifest["station_matrix"]
    matrix_raw = _read(bundle, matrix["path"])
    if _digest(matrix_raw) != matrix["sha256"]:
        raise ValueError("historical station matrix SHA-256 mismatch")
    rows = json.loads(matrix_raw)["stations"]
    if len(rows) != 2:
        raise ValueError("historical station matrix must retain exactly two rows")
    profiles = tuple(_profile(row, spec, data, sha256, matrix["sha256"]) for row, spec in zip(rows, _SPECS, strict=True))
    roles = ({"path": _MANIFEST, "sha256": sha256, "role": "additional_input_manifest"},
             {"path": matrix["path"], "sha256": matrix["sha256"], "role": "parameter_row_index"},
             *(dict(member, role=_role(member["path"])) for member in members))
    return HistoricalInputs(profiles, sha256, tuple(dict(member) for member in members), roles, matrix["sha256"], manifest)


def _same_nominal_position(station: dict, profile: dict) -> bool:
    """The publisher's one-minute location cell, never a guessed precise gauge."""
    return all(isinstance(station.get(key), (int, float))
               and round(station[key] * 60) == round(profile[key] * 60) for key in ("lat", "lon"))


def _source_tuple(record: dict) -> tuple:
    return record.get("source"), record.get("source_version"), record.get("source_record_id")


def _historical_reference(stations: list[dict], profile: dict, licences: set[str]) -> dict:
    offsets = profile["subordinate_offsets"]
    binding = offsets["provenance"]["reference_binding"]
    candidates = [st for st in stations if st.get("status") == "active"
                  and (st.get("aliases", {}).get("noaa") == binding["noaa_id"]
                       or any(_source_tuple(record) == ("noaa", cs.get("source_version"), binding["noaa_id"])
                              for record in st.get("source_records", [])
                              for cs in st.get("constant_sets", []) if cs.get("source") == "noaa"))]
    if len(candidates) != 1:
        raise ValueError(f"{offsets['offset_id']}: missing or ambiguous NOAA reference {binding['noaa_id']}")
    reference = candidates[0]
    if (reference["station_id"] != binding["station_id"] or reference.get("type") != "reference"
            or reference.get("aliases", {}).get("noaa") != binding["noaa_id"]):
        raise ValueError(f"{offsets['offset_id']}: reference identity differs from the approved primary binding")
    selected = next((cs for cs in reference.get("constant_sets", [])
                     if cs.get("set_id") == reference.get("recommended_set_id")), {})
    datum = selected.get("datum", {})
    basis = datum.get("basis", {}).get("mllw", {})
    if (offsets["licence_id"] not in licences or selected.get("licence_id") not in licences):
        raise ValueError(f"{offsets['offset_id']}: own/reference licence must resolve")
    if (selected.get("source") != "noaa" or selected.get("quantity") != "water_level"
            or datum.get("chart_datum") != "mllw" or "mllw" not in datum.get("named", {})
            or basis.get("kind") != "published" or basis.get("method") != "source"):
        raise ValueError(f"{offsets['offset_id']}: reference requires actual NOAA water-level source and published MLLW")
    return reference


def _historical_target(stations: list[dict], profile: dict) -> dict | None:
    record_key = _source_tuple(profile["source_records"][0])
    lineage = [st for st in stations if st.get("status") == "active"
               and any(_source_tuple(record) == record_key for record in st.get("source_records", []))]
    named = [st for st in stations if st.get("status") == "active"
             and " ".join(st.get("name", "").casefold().split()) == " ".join(profile["name"].casefold().split())
             and _same_nominal_position(st, profile)
             and not st.get("current_offsets")
             and not (st.get("constant_sets") and all(cs.get("quantity") == "currents" for cs in st["constant_sets"]))]
    candidates = {st["station_id"]: st for st in [*lineage, *named]}
    if len(candidates) > 1:
        raise ValueError(f"{profile['name']}: ambiguous historical target identity")
    target = next(iter(candidates.values()), None)
    if target is not None:
        if not _same_nominal_position(target, profile):
            raise ValueError(f"{profile['name']}: existing source lineage has conflicting nominal position")
        if target.get("country") != "USA":
            raise ValueError(f"{profile['name']}: ambiguous country binding for the US Table2 row")
    return target


def apply_noaa_historical_offsets(release: dict, inputs: HistoricalInputs, *, build_commit: str) -> dict:
    """Atomically retain both source profiles; do not select/rerank predictions."""
    stations = release["stations"]
    licences = {lic["licence_id"] for lic in release["licences"]}
    if len(inputs.profiles) != 2 or len(inputs.members) != 56:
        raise ValueError("historical augmentation requires both verified rows and all 56 members")
    planned = list(stations)
    taken = {st["station_id"] for st in stations}
    created = 0
    rows = []
    finder = None
    for profile in inputs.profiles:
        reference = _historical_reference(stations, profile, licences)
        target = _historical_target(stations, profile)
        off = profile["subordinate_offsets"]
        if target is None:
            if finder is None:
                finder = TimezoneFinder()
            timezone = finder.timezone_at(lat=profile["lat"], lng=profile["lon"])
            if timezone is None:
                raise ValueError(f"{profile['name']}: nominal-position display timezone is unresolved")
            provenance = off["provenance"]
            founder = f"noaa:annual-tide-tables-2020:{provenance['book']}:table2:{provenance['annual_table_row']}"
            sid = sm.new_id(founder, taken)
            taken.add(sid)
            target = {"station_id": sid, "status": "active", "name": profile["name"], "country": "USA",
                      "lat": profile["lat"], "lon": profile["lon"], "timezone": timezone,
                      "type": "subordinate", "aliases": {}, "constant_sets": [], "recommended_set_id": None}
            index = len(planned)
            created += 1
        else:
            index = next(i for i, st in enumerate(stations) if st["station_id"] == target["station_id"])
            target = deepcopy(target)
        old_offsets = target.get("subordinate_offsets")
        if old_offsets is not None:
            numbers = ("height_adjusted_type", "time_offset_high_min", "time_offset_low_min",
                       "height_offset_high", "height_offset_low")
            if (old_offsets.get("offset_id") != off["offset_id"]
                    or any(old_offsets.get(key) != off[key] for key in numbers)
                    or old_offsets.get("provenance") != off["provenance"]):
                raise ValueError(f"{profile['name']}: existing ordinary/conflicting offsets must not be overwritten")
            # Keep measured vintage findings from a previous import; no optimistic reset.
        elif target.get("current_offsets"):
            raise ValueError(f"{profile['name']}: existing current offsets cannot be overwritten by historical tides")
        else:
            target["subordinate_offsets"] = deepcopy(off)
            if not target.get("constant_sets") and target.get("recommended_set_id") is None:
                target["type"] = "subordinate"
        record = profile["source_records"][0]
        original = [source for source in target.get("source_records", []) if _source_tuple(source) == _source_tuple(record)]
        if original and (len(original) != 1 or original[0] != record):
            raise ValueError(f"{profile['name']}: existing original source tuple conflicts with the sealed row")
        if not original:
            target.setdefault("source_records", []).append(deepcopy(record))
        if index == len(planned):
            planned.append(target)
        else:
            planned[index] = target
        selected = next(cs for cs in reference["constant_sets"] if cs["set_id"] == reference["recommended_set_id"])
        rows.append({"station_id": target["station_id"], "offset_id": off["offset_id"],
                     "source_record_id": off["source_record_id"], "reference_station_id": reference["station_id"],
                     "reference_set_id": selected["set_id"], "reference_source_version": selected["source_version"],
                     "source_version": off["source_version"], "licence_id": off["licence_id"]})
    versions = dict(release["release"].get("source_versions", {}))
    if versions.get("noaa-historical", "annual-tide-tables-2020") != "annual-tide-tables-2020":
        raise ValueError("historical augmentation collection version conflicts with existing metadata")
    versions["noaa-historical"] = "annual-tide-tables-2020"
    release["stations"] = planned
    release["release"]["source_versions"] = versions
    return {"source_manifest_sha256": inputs.manifest_sha256, "matrix_sha256": inputs.matrix_sha256,
            "build_commit": build_commit, "profile_count": 2, "verified_member_count": 56,
            "created_stations": created, "reused_stations": 2 - created,
            "source_records": rows, "members": list(inputs.members), "input_roles": list(inputs.input_roles),
            "count_unit": "two retained published parameter rows; 56 captured evidence artifacts, not 56 places",
            "source_status": "historical2020; inferred legacy associations; retired profiles; current validity unestablished; accuracy unverified"}
