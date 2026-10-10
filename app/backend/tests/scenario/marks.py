import pytest


def needs(*issues: int) -> pytest.MarkDecorator:
    """Strict xfail until the named backend issues land; remove the mark when they do."""
    refs = ", ".join(f"#{n}" for n in issues)
    return pytest.mark.xfail(strict=True, reason=f"needs {refs}")
