"""Tests of the harness itself: protocol shape, strict-xfail hygiene, traceability."""

import ast
import re
from pathlib import Path

import pytest

from .driver import ScenarioDriver

HERE = Path(__file__).parent
DOC = HERE.parents[3] / "docs" / "product" / "test-strategy.md"
METHODS = {
    "reset",
    "trigger_cancellation",
    "passenger_reply",
    "passenger_tap",
    "get_journey",
    "transcript",
    "advance_clock",
}


def _scenario_tests() -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for path in sorted(HERE.glob("test_s[1-5]_*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                found[f"{path.name}::{node.name}"] = node
    return found


def test_driver_protocol_exposes_the_documented_surface():
    public = {n for n in vars(ScenarioDriver) if not n.startswith("_")}
    assert METHODS <= public
    for name in METHODS:
        assert getattr(ScenarioDriver, name).__doc__, f"{name} needs a docstring"


def test_there_is_one_scenario_module_per_scenario():
    assert [p.name[:8] for p in sorted(HERE.glob("test_s[1-5]_*.py"))] == [
        f"test_s{n}_" for n in range(1, 6)
    ]


def test_scenario_tests_never_use_bare_skip_or_xfail_marks():
    # Only @needs(<issue>) (strict xfail with a reason) may defer a scenario test.
    for test_id, node in _scenario_tests().items():
        for deco in node.decorator_list:
            text = ast.unparse(deco)
            assert "skip" not in text and "xfail" not in text, test_id
            if "needs" in text:
                assert isinstance(deco, ast.Call), test_id
                assert deco.args and all(isinstance(a, ast.Constant) for a in deco.args), test_id


def test_xfail_marks_are_strict():
    marker = pytest.mark.xfail(strict=True)
    from .marks import needs

    assert needs(10).kwargs["strict"] is marker.kwargs["strict"]
    assert needs(10, 11).kwargs["reason"] == "needs #10, #11"


def test_traceability_table_covers_every_scenario_test_and_nothing_else():
    cited = set(re.findall(r"`(test_s[1-5]_\w+\.py::test_\w+)`", DOC.read_text()))
    assert cited == set(_scenario_tests())


def test_every_brief_fact_in_the_table_is_asserted_by_a_test_text():
    sources = "".join(p.read_text() for p in HERE.glob("test_s[1-5]_*.py"))
    for fact in (
        "AC1240", "08:15", "14A", "HT8842", "AC1252", "2A", "AC0800",
        "135.00", "290.00", "80.00", "2209", "2203", "diamond",
    ):  # fmt: skip
        assert fact.lower() in sources.lower() or fact in sources, fact
