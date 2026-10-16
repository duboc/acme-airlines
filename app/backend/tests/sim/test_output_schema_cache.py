"""The MCP client checks a tool's output schema once, and still validates every result (#438)."""

from types import SimpleNamespace

import pytest
from jsonschema.validators import Draft202012Validator

from acme.sim import client as sim_client
from acme.sim.client import _CachingSession

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


def session(schema=SCHEMA):
    s = _CachingSession.__new__(_CachingSession)  # no transport: only the validation is used
    s._tool_output_schemas = {"t": schema}  # noqa: SLF001
    return s


def result(content):
    return SimpleNamespace(structuredContent=content)


async def test_schema_is_checked_once_for_many_valid_results(monkeypatch):
    sim_client._VALIDATORS.clear()  # noqa: SLF001
    calls = []
    real = Draft202012Validator.check_schema
    monkeypatch.setattr(
        Draft202012Validator, "check_schema", classmethod(lambda c, s: calls.append(1) or real(s))
    )
    for s in (session(), session()):  # two sessions share the checked validator
        for i in range(5):
            await s._validate_tool_result("t", result({"n": i}))  # noqa: SLF001
    assert len(calls) == 1


async def test_an_invalid_result_is_still_rejected():
    with pytest.raises(RuntimeError, match="Invalid structured content returned by tool t"):
        await session()._validate_tool_result("t", result({"n": "x"}))  # noqa: SLF001


async def test_a_missing_structured_content_is_rejected():
    with pytest.raises(RuntimeError, match="did not return"):
        await session()._validate_tool_result("t", result(None))  # noqa: SLF001


async def test_an_invalid_schema_is_a_runtime_error():
    with pytest.raises(RuntimeError, match="Invalid schema for tool t"):
        await session({"type": 12})._validate_tool_result("t", result({}))  # noqa: SLF001


async def test_a_tool_without_an_output_schema_is_not_validated():
    s = session(None)
    await s._validate_tool_result("t", result({"anything": 1}))  # noqa: SLF001
