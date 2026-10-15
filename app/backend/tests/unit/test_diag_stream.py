"""The ACME_DIAG handler follows `sys.stderr` instead of the stream of its first use (#280)."""

import io
import logging
import sys

from acme import telemetry


def test_diag_writes_to_the_current_stderr_even_after_the_first_one_is_closed(monkeypatch, capsys):
    monkeypatch.setattr(telemetry.DIAG, "handlers", [])  # fresh handler, as in a new process
    first = io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    telemetry.diag("probe", n=1)
    first.close()  # what pytest does to its per-test capture stream
    second = io.StringIO()
    monkeypatch.setattr(sys, "stderr", second)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    telemetry.diag("probe", n=2)
    assert '"n": 2' in second.getvalue()
    telemetry.DIAG.handlers.clear()
