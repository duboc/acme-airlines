"""Stage the Agent Engine deploy artifacts locally, the way the SDK uploads them (#185).

`python -m acme.engines.stage OUTDIR` writes what Agent Engine would receive, so a clean
virtualenv can load it offline (`scripts/engine_smoke.py`):

- `requirements.txt`   exactly `engine_requirements()` plus `cloudpickle` (the SDK appends it)
- `extracted/`         `dependencies.tar.gz` unpacked: `tar.add(path)` for each `extra_packages`
                       entry, relative to `package_root()` (so `acme/` must sit at the root)
- `customer.pkl`, `sales.pkl`   the pickled engine objects

No GCP access is needed.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
from pathlib import Path

from acme.engines import customer, sales
from acme.engines.deploy import package_root


def sdk_tarball(extra_packages: list[str], cwd: Path) -> bytes:
    """What the SDK uploads: `tar.add(path)` per extra package, relative to the cwd."""
    buf = io.BytesIO()
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        with tarfile.open(fileobj=buf, mode="w|gz") as tar:
            for path in extra_packages:
                tar.add(path, filter=lambda ti: None if "__pycache__" in ti.name else ti)
    finally:
        os.chdir(previous)
    return buf.getvalue()


def stage(out: Path) -> None:
    import cloudpickle

    out.mkdir(parents=True, exist_ok=True)
    (out / "requirements.txt").write_text(
        "\n".join([*customer.engine_requirements(), "cloudpickle"]) + "\n"
    )
    data = sdk_tarball(customer.engine_extra_packages(), package_root())
    tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractall(out / "extracted", filter="data")
    (out / "customer.pkl").write_bytes(cloudpickle.dumps(customer.build_customer_engine_app()))
    (out / "sales.pkl").write_bytes(cloudpickle.dumps(sales.build_sales_engine_app()))


if __name__ == "__main__":
    stage(Path(sys.argv[1]))
    print(f"staged in {sys.argv[1]}")
