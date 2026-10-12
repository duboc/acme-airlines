"""Deploy or update both Agent Engine apps in place (#163): `python -m acme.engines.deploy`.

Looks up each engine by display name (update if one exists, create if none, fail on duplicates,
and nothing is created if either lookup fails), Customer first, then Sales pointed at it. Run from
`app/backend` (`extra_packages` is relative to it). Authenticates with ADC only. The `sdk` is
injected so tests never touch GCP.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any, Protocol

from acme.engines import customer, sales

TELEMETRY_ENV = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"


class DeployError(RuntimeError):
    pass


class AgentEngineSdk(Protocol):  # the surface of `vertexai.agent_engines` that we use
    def list(self, *, filter: str = "") -> Any: ...  # noqa: A002
    def create(self, **kwargs: Any) -> Any: ...
    def update(self, resource_name: str, **kwargs: Any) -> Any: ...


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
            "GOOGLE_CLOUD_PROJECT": env["GOOGLE_CLOUD_PROJECT"],
            "GOOGLE_CLOUD_LOCATION": "global",  # model calls stay on the global endpoint
            TELEMETRY_ENV: "true",  # tracing to Cloud Trace (this SDK has no enable_tracing arg)
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
    sal = _deploy_one(
        sdk, sales_existing, sales.build_sales_engine_app(),
        **_settings(env, sales.DISPLAY_NAME, sales_env),
    )  # fmt: skip
    return {customer.DISPLAY_NAME: cust, sales.DISPLAY_NAME: sal}


def main() -> int:
    import vertexai
    from vertexai import agent_engines

    env = os.environ
    vertexai.init(
        project=env["GOOGLE_CLOUD_PROJECT"],
        location=env["AGENT_ENGINE_LOCATION"],
        staging_bucket=f"gs://{env['AGENT_ENGINE_STAGING_BUCKET'].removeprefix('gs://')}",
    )
    try:
        names = deploy(agent_engines, env)
    except DeployError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
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
