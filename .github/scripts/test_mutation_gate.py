"""Tests for mutation_gate.py (stdlib unittest)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import mutation_gate as gate  # noqa: E402

CLEAN = {"killed": 10, "survived": 0, "total": 10, "no_tests": 0, "suspicious": 0, "timeout": 0,
         "skipped": 0, "segfault": 0}  # fmt: skip
RESULTS = "    acme.policy.margin_opt.x_recovery_quote__mutmut_3: survived\n"


class MutationGate(unittest.TestCase):
    def test_a_clean_run_passes_and_says_so(self):
        code, md = gate.evaluate(CLEAN, "")
        self.assertEqual(code, 0)
        self.assertIn("10 of 10 mutants killed", md)

    def test_a_surviving_mutant_fails_and_is_listed_in_the_summary(self):
        code, md = gate.evaluate({**CLEAN, "killed": 9, "survived": 1}, RESULTS)
        self.assertEqual(code, 1)
        self.assertIn("acme.policy.margin_opt.x_recovery_quote__mutmut_3", md)
        self.assertIn("mutmut show", md)

    def test_untested_suspicious_and_crashed_mutants_also_fail(self):
        for key in ("no_tests", "suspicious", "segfault"):
            code, _ = gate.evaluate({**CLEAN, "killed": 9, key: 1}, "")
            self.assertEqual(code, 1, key)

    def test_timeouts_alone_do_not_fail_but_are_reported(self):
        code, md = gate.evaluate({**CLEAN, "killed": 8, "timeout": 2}, "")
        self.assertEqual(code, 0)
        self.assertIn("2 timed out", md)

    def test_an_empty_run_is_a_failure_not_a_pass(self):
        code, md = gate.evaluate({**CLEAN, "killed": 0, "total": 0}, "")
        self.assertEqual(code, 1)
        self.assertIn("no mutants", md)

    def test_main_reads_the_stats_file_and_results_and_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as d:
            stats, results, summary = (Path(d) / n for n in ("s.json", "r.txt", "out.md"))
            stats.write_text(json.dumps({**CLEAN, "killed": 9, "survived": 1}))
            results.write_text(RESULTS)
            code = gate.main([str(stats), str(results), str(summary)])
            self.assertEqual(code, 1)
            self.assertIn("x_recovery_quote__mutmut_3", summary.read_text())


if __name__ == "__main__":
    unittest.main()
