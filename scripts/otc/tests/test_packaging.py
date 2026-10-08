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
    assert "otc_pipeline/schema/otc-0.2.schema.json" in names, names
    shipped = zipfile.ZipFile(wheels[0]).read("otc_pipeline/schema/otc-0.2.schema.json")
    assert shipped == (ROOT / "schema" / "otc-0.2.schema.json").read_bytes()
