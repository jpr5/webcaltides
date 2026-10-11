"""Only the loader (otc_pipeline/datum_partition.py) and the sealed T1 harness may read the sealed
partition files (datums spec §5.1: after the seal, tuning code may use the tuning partition only).
Every other module goes through `datum_partition.load`, which hands out one partition by name."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# Paths (relative to the repo) allowed to name the sealed files. The sealed T1 harness (slot DT1) adds itself
# here; this test file names them only as search patterns.
ALLOWED = {"scripts/otc/otc_pipeline/datum_partition.py", "scripts/otc/tests/test_datum_partition_access.py"}
SEALED_NAME = re.compile(r"partitions\.json|t1_stations\.json")
SUFFIXES = {".py", ".rb", ".sh", ".zsh", ".bash", ".js", ".mjs", ".ts", ".ipynb", ".toml", ".cfg", ".ini", ".rake"}


def test_no_other_module_names_the_sealed_partition_files():
    offenders = []
    for root in (REPO / "scripts", REPO / "bin", REPO / "lib", REPO / "spec"):
        if not root.exists():
            continue
        for f in root.rglob("*"):
            rel = f.relative_to(REPO).as_posix()
            if not f.is_file() or f.suffix not in SUFFIXES or rel in ALLOWED or "/.venv/" in rel or "__pycache__" in rel:
                continue
            if SEALED_NAME.search(f.read_text(encoding="utf-8", errors="replace")):
                offenders.append(rel)
    assert offenders == [], f"read the sealed lists through datum_partition.load, not directly: {offenders}"


def test_the_scan_sees_a_direct_reader(tmp_path, monkeypatch):
    # Guard against a vacuous scan: a file naming the sealed list is found.
    bad = REPO / "scripts" / "otc" / "otc_pipeline" / "_access_probe_tmp.py"
    bad.write_text('import json; json.load(open("data/datums/t1_stations.json"))\n')
    try:
        try:
            test_no_other_module_names_the_sealed_partition_files()
        except AssertionError as e:
            assert "_access_probe_tmp.py" in str(e)
        else:
            raise AssertionError("the scan did not flag a direct reader")
    finally:
        bad.unlink()
