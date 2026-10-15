"""Verify that every ACME_* and LLM_* environment variable in code is documented (#285)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "app/backend/src"
COMPOSE = REPO / "app/compose.yaml"
DEPLOY_DOC = REPO / "docs/product/deploy.md"

VAR_RE = re.compile(r"^(?:ACME|LLM)_[A-Z0-9_]+$")
ENV_READ_RE = re.compile(
    r"""(?:os\.environ(?:\.get)?|os\.getenv|env\.get|env\[|_ENV\s*=)[(\[\s]*["']((?:ACME|LLM)_[A-Z0-9_]+)["']"""
)
EXPECTED_COLUMNS = [
    "Name",
    "Purpose",
    "Default",
    "Required?",
    "Fail-closed behaviour",
    "Where read",
]


def _parse_config_table(markdown: str) -> tuple[list[str], dict[str, dict[str, str]]]:
    section = re.split(r"(?m)^#", markdown.split("## Configuration reference\n", 1)[1], maxsplit=1)[
        0
    ]
    table_lines = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows: dict[str, dict[str, str]] = {}
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(headers), f"malformed table row: {line}"
        assert all(cells), f"empty cell in table row: {line}"
        name = cells[0].strip("`")
        assert name not in rows, f"duplicate variable in table: {name}"
        rows[name] = dict(zip(headers, cells, strict=True))
    return headers, rows


def _code_env_vars() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text()
        found.update(ENV_READ_RE.findall(text))
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and VAR_RE.match(node.value)
            ):
                found.add(node.value)
            elif isinstance(node, ast.keyword) and node.arg and VAR_RE.match(node.arg):
                found.add(node.arg)
    return found


def test_every_acme_and_llm_variable_is_documented_in_the_configuration_reference():
    headers, table = _parse_config_table(DEPLOY_DOC.read_text())
    assert headers == EXPECTED_COLUMNS

    code_vars = _code_env_vars()
    documented_vars = set(table)
    assert code_vars - documented_vars == set(), (
        f"undocumented ACME_*/LLM_* variables read in app/backend/src: "
        f"{sorted(code_vars - documented_vars)}"
    )
    assert documented_vars - code_vars == set(), (
        f"stale ACME_*/LLM_* variables in docs/product/deploy.md not found in code: "
        f"{sorted(documented_vars - code_vars)}"
    )

    compose_vars = set(re.findall(r"\b((?:ACME|LLM)_[A-Z0-9_]+)\b", COMPOSE.read_text()))
    assert compose_vars <= documented_vars

    for name, row in table.items():
        modules = re.findall(r"`(acme/[^`]+\.py)`", row["Where read"])
        assert modules, f"{name} must cite at least one acme/...py file in 'Where read'"
        for rel in modules:
            mod_path = SRC / rel
            assert mod_path.is_file(), f"{name} cites missing file {rel}"
            assert name in mod_path.read_text(), f"{name} not found in cited file {rel}"
