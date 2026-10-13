"""Deploy or update both Agent Engine apps in place (#163): `python -m acme.engines.deploy`.

Looks up each engine by display name (update if one exists, create if none, fail on duplicates,
and nothing is created if either lookup fails), Customer first, then Sales pointed at it.
`main()` runs from `package_root()` so `extra_packages=["acme"]` puts `acme/` at the tarball root
(#185). Authenticates with ADC only. The `sdk` is injected so tests never touch GCP.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from acme.engines import customer, sales
from acme.llm import config as llm_config

# Never put GOOGLE_CLOUD_* names in the engine env: Agent Engine reserves and injects them and
# rejects the deploy with a 400 (#190). Our own non-reserved names carry the settings instead.
TRACING_ENV = "ACME_ENABLE_TRACING"
GENAI_LOCATION_ENV = "ACME_GENAI_LOCATION"
GENAI_PROJECT_ENV = "ACME_GENAI_PROJECT"
# SQLite file shared by the processes of one container (#200). /tmp is container-local, so the
# engines must run as ONE container: verify_started fails unless max_instances == 1.
STATE_DB_ENV = "ACME_STATE_DB"
STATE_DB_PATH = "/tmp/acme-state/state.db"


def package_root() -> Path:
    """The directory that contains the `acme` package (`app/backend/src` in the source tree).

    `extra_packages` paths are resolved from here: the SDK tars them by path, and the container
    needs `acme/` at the tarball root (#185).
    """
    return Path(__file__).resolve().parents[2]


class DeployError(RuntimeError):
    pass


class AgentEngineSdk(Protocol):  # the surface of `vertexai.agent_engines` that we use
    def list(self, *, filter: str = "") -> Any: ...  # noqa: A002
    def create(self, **kwargs: Any) -> Any: ...
    def update(self, resource_name: str, **kwargs: Any) -> Any: ...
    def get(self, resource_name: str) -> Any: ...


def _find(sdk: AgentEngineSdk, display_name: str) -> str | None:
    hits = [
        e.resource_name
        for e in sdk.list(filter=f'display_name="{display_name}"')
        if e.gca_resource.display_name == display_name
    ]
    if len(hits) > 1:
        raise DeployError(f"{len(hits)} engines named {display_name!r}: {hits}; refusing to guess")
    return hits[0] if hits else None


def _settings(env: Mapping[str, str], name: str, extra_env: dict[str, str]) -> dict[str, Any]:
    return {
        "display_name": name,
        "requirements": customer.engine_requirements(),
        "extra_packages": customer.engine_extra_packages(),
        "service_account": env["AGENT_ENGINE_SA"],
        "env_vars": {
            "LLM_MODE": "vertex",
            GENAI_LOCATION_ENV: "global",  # model calls stay on the global endpoint
            GENAI_PROJECT_ENV: llm_config.PROJECT,  # the injected GOOGLE_CLOUD_PROJECT is a number
            TRACING_ENV: "true",  # Cloud Trace export, installed by acme.telemetry
            STATE_DB_ENV: STATE_DB_PATH,
            **extra_env,
        },
    }


def _deploy_one(sdk: AgentEngineSdk, existing: str | None, agent: Any, **kwargs: Any) -> str:
    if existing:
        return sdk.update(existing, agent_engine=agent, **kwargs).resource_name
    return sdk.create(agent_engine=agent, **kwargs).resource_name


def deploy(sdk: AgentEngineSdk, env: Mapping[str, str]) -> dict[str, str]:
    cust_existing = _find(sdk, customer.DISPLAY_NAME)
    sales_existing = _find(sdk, sales.DISPLAY_NAME)  # both lookups before any write
    # The Customer engine holds in-memory world state: pin it to one instance (demo limitation).
    cust = _deploy_one(
        sdk, cust_existing, customer.build_customer_engine_app(),
        min_instances=1, max_instances=1, **_settings(env, customer.DISPLAY_NAME, {}),
    )  # fmt: skip
    sales_env = {"ENGINE_MODE": "agent-engine", "ACME_CUSTOMER_ENGINE": cust}
    sal = _deploy_one(  # the radar's sent-set is container-local state too: one container
        sdk, sales_existing, sales.build_sales_engine_app(),
        min_instances=1, max_instances=1, **_settings(env, sales.DISPLAY_NAME, sales_env),
    )  # fmt: skip
    return {customer.DISPLAY_NAME: cust, sales.DISPLAY_NAME: sal}


def _max_instances(engine: Any) -> Any:
    try:
        return engine.gca_resource.spec.deployment_spec.max_instances
    except AttributeError:
        return None


def verify_started(sdk: AgentEngineSdk, names: Mapping[str, str]) -> None:
    """Ask every deployed engine for its `diagnostics`; a failed start-up fails the deploy and
    prints the (scrubbed) error, so a broken engine never counts as green (#185)."""
    for display, resource in names.items():
        try:
            engine = sdk.get(resource)
            diag = engine.diagnostics()
        except Exception as exc:  # noqa: BLE001
            raise DeployError(f"{display}: diagnostics call failed: {exc}") from exc
        max_instances = _max_instances(engine)
        if max_instances != 1:  # state is container-local (#200); unreadable also fails
            raise DeployError(
                f"{display}: max_instances is {max_instances!r} but must be 1: the demo state "
                "lives on one container's disk and a second container would not share it"
            )
        if diag.get("startup_error") or diag.get("status") != "ready":
            status, error = diag.get("status"), diag.get("startup_error")
            raise DeployError(f"{display} did not start (status {status}):\n{error}")


BUCKET_HINT = (
    "403 on the staging bucket: the SDK calls storage.buckets.get (vertexai _agent_engines.py "
    "_get_gcs_bucket), which the deployer SA lacks. Needs an operator grant; see issue #186."
)


def main() -> int:
    import vertexai
    from vertexai import agent_engines

    env = os.environ
    vertexai.init(
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=env["AGENT_ENGINE_LOCATION"],
        staging_bucket=f"gs://{env['AGENT_ENGINE_STAGING_BUCKET'].removeprefix('gs://')}",
    )
    previous = os.getcwd()
    os.chdir(package_root())  # extra_packages=["acme"] is relative to here
    try:
        names = deploy(agent_engines, env)
        verify_started(agent_engines, names)
    except DeployError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "Forbidden" and "storage.googleapis.com" in str(exc):
            print(f"::error::{BUCKET_HINT}", file=sys.stderr)
            return 1
        raise
    finally:
        os.chdir(previous)
    ids = ",".join(f"{n}={r.rsplit('/', 1)[-1]}" for n, r in names.items())
    for n, r in names.items():
        print(f"{n}: {r}")
    if out := env.get("GITHUB_OUTPUT"):
        with open(out, "a") as fh:
            fh.write(
                f"customer={names[customer.DISPLAY_NAME]}\nsales={names[sales.DISPLAY_NAME]}\n"
            )
            fh.write(f"engines={ids}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
