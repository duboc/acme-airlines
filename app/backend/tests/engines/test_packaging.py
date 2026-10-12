"""#185: the engine container could not import `acme` (the SDK tars extra_packages by path).

`vertexai` does `tar.add(path)`, so `extra_packages=["src/acme"]` produced `src/acme/...` in
dependencies.tar.gz: no top-level `acme` in the container, and unpickling the engine failed with
ModuleNotFoundError. These tests build the tarball exactly as the SDK does and import from it.
"""

import io
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path

from acme.engines import deploy as mod
from acme.engines.customer import engine_extra_packages
from acme.engines.stage import sdk_tarball, stage


def test_tarball_has_acme_at_its_root():
    names = tarfile.open(
        fileobj=io.BytesIO(sdk_tarball(engine_extra_packages(), mod.package_root())), mode="r:gz"
    ).getnames()
    assert "acme/__init__.py" in names and "acme/engines/customer.py" in names
    assert not any(n.startswith("src/") for n in names)


def test_container_can_import_acme_from_the_extracted_tarball(tmp_path):
    data = sdk_tarball(engine_extra_packages(), mod.package_root())
    tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractall(tmp_path, filter="data")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import acme, acme.engines.customer as c;"
        "print(acme.__file__)"
    )
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", code, str(tmp_path)],
        capture_output=True, text=True, check=True, cwd=tmp_path,
    ).stdout.strip()  # fmt: skip
    assert Path(out).is_relative_to(tmp_path), f"imported {out}, not the shipped copy"


def test_the_old_src_acme_layout_had_no_top_level_acme(tmp_path):
    """Why the container failed: `src/acme` from app/backend puts the package under `src/`."""
    backend = mod.package_root().parent  # app/backend when running from the source tree
    names = tarfile.open(
        fileobj=io.BytesIO(sdk_tarball(["src/acme"], backend)), mode="r:gz"
    ).getnames()
    assert "acme/__init__.py" not in names and "src/acme/__init__.py" in names


def test_main_runs_from_the_package_root_so_relative_extra_packages_resolve(monkeypatch):
    seen = {}

    class Sdk:
        def list(self, **_):
            seen["cwd"] = os.getcwd()
            raise RuntimeError("stop")

    fake = types.SimpleNamespace(init=lambda **_: None, agent_engines=Sdk())
    monkeypatch.setitem(sys.modules, "vertexai", fake)
    monkeypatch.setitem(sys.modules, "vertexai.agent_engines", fake.agent_engines)
    for k, v in {
        "AGENT_ENGINE_SA": "sa",
        "GOOGLE_CLOUD_PROJECT": "acme-airlines-factory",
        "AGENT_ENGINE_LOCATION": "us-central1",
        "AGENT_ENGINE_STAGING_BUCKET": "stage",
    }.items():
        monkeypatch.setenv(k, v)
    before = os.getcwd()
    try:
        mod.main()
    except RuntimeError:
        pass
    assert Path(seen["cwd"]).resolve() == mod.package_root().resolve()
    assert os.getcwd() == before  # restored


def test_stage_writes_what_the_sdk_would_upload(tmp_path):
    stage(tmp_path)
    assert (tmp_path / "extracted/acme/engines/customer.py").is_file()
    assert (tmp_path / "customer.pkl").stat().st_size > 0
    assert (tmp_path / "sales.pkl").stat().st_size > 0
    reqs = (tmp_path / "requirements.txt").read_text().split()
    assert "cloudpickle" in reqs and any(r.startswith("google-adk") for r in reqs)
