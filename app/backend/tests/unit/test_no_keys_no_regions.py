"""Hygiene: no API keys, regional Vertex endpoints or non-global locations in app/."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[3]
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".toml", ".yml", ".yaml", ".sh", ".env"}
# "mutants" is the git-ignored mutmut working copy (#281)
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", "dist", "mutants"}
THIS_FILE = Path(__file__).resolve()
# Explicit allowlist: the refusal checks themselves, and tests (which assert refusals).
ALLOWLIST = {APP / "backend/src/acme/llm/config.py"}
ALLOWED_DIRS = {APP / "backend/tests"}

RULES = {
    "API key env var": re.compile(r"GOOGLE_API_KEY|GEMINI_API_KEY"),
    "api_key=": re.compile(r"api_key\s*="),
    "regional aiplatform host": re.compile(r"[a-z]+-[a-z]+\d+-aiplatform\.googleapis\.com"),
    "non-global location": re.compile(
        r"""(?:location|GOOGLE_CLOUD_LOCATION)["']?\s*[=:]\s*["'](?!global["'])[\w-]+["']""",
        re.IGNORECASE,
    ),
}


def _files():
    for p in APP.rglob("*"):
        if (
            p.is_file()
            and p.suffix in SUFFIXES
            and not (set(p.relative_to(APP).parts) & SKIP_DIRS)
            and p not in ALLOWLIST
            and not any(d in p.parents for d in ALLOWED_DIRS)
        ):
            yield p


def scan(files):
    hits = []
    for p in files:
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            hits += [f"{p}:{n}: {name}" for name, rx in RULES.items() if rx.search(line)]
    return hits


def test_app_is_clean():
    assert scan(_files()) == []


def test_rules_catch_violations(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text(
        "os.environ['GEMINI_API_KEY']\n"
        "c(api_key='k')\n"
        "u='https://us-central1-aiplatform.googleapis.com'\n"
        "location='us-east1'\n"
    )
    assert len(scan([bad])) == 4
    ok = tmp_path / "y.py"
    ok.write_text("location='global'\n")
    assert scan([ok]) == []
