"""The built wheel must carry the schema, so a non-editable install can validate."""
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the wheel")
def test_wheel_ships_the_schema(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
                   check=True, capture_output=True)
    wheels = list(tmp_path.glob("otc_pipeline-*.whl"))
    assert len(wheels) == 1, wheels
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert "otc_pipeline/schema/otc-0.5.schema.json" in names, names
    shipped = zipfile.ZipFile(wheels[0]).read("otc_pipeline/schema/otc-0.5.schema.json")
    assert shipped == (ROOT / "schema" / "otc-0.5.schema.json").read_bytes()


def _pyproject(name):
    import tomllib
    return tomllib.loads((ROOT.parent / name / "pyproject.toml").read_text())


@pytest.mark.parametrize("name", ["otc"])
def test_build_backend_is_pinned(name):
    requires = _pyproject(name)["build-system"]["requires"]
    assert requires and all("==" in r for r in requires), requires


def test_gesla_is_never_resolved_from_an_index():
    # "gesla" is unpublished on PyPI: a bare requirement would let pip fetch whatever is
    # registered under that name. It must be a direct reference to the sibling project.
    deps = _pyproject("otc")["project"]["dependencies"]
    gesla = [d for d in deps if d.split("@")[0].split("[")[0].strip().lower() == "gesla"]
    assert len(gesla) == 1 and "@" in gesla[0], gesla
    assert gesla[0].split("@", 1)[1].strip() == "{root:parent:uri}/gesla"


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the wheel")
def test_wheel_requires_gesla_by_file_reference(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
                   check=True, capture_output=True)
    wheel = next(tmp_path.glob("otc_pipeline-*.whl"))
    meta = next(n for n in zipfile.ZipFile(wheel).namelist() if n.endswith("METADATA"))
    reqs = [line for line in zipfile.ZipFile(wheel).read(meta).decode().splitlines()
            if line.startswith("Requires-Dist: gesla")]
    assert reqs == [f"Requires-Dist: gesla @ {(ROOT.parent / 'gesla').as_uri()}"], reqs
