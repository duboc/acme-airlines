"""Tests for check_action_pins.py (stdlib unittest; run: python3 -m unittest discover .github/scripts)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import check_action_pins as pins  # noqa: E402

SHA = "11d5960a326750d5838078e36cf38b85af677262"


def problems(line: str) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / "w.yml"
        wf.write_text(f"jobs:\n  j:\n    steps:\n      {line}\n")
        return pins.check_file(wf)


class PinCheck(unittest.TestCase):
    def test_a_sha_pin_with_a_version_comment_passes(self):
        self.assertEqual(problems(f"- uses: actions/checkout@{SHA} # v4.2.2"), [])

    def test_a_mutable_tag_is_rejected(self):
        self.assertTrue(problems("- uses: actions/checkout@v4"))

    def test_a_branch_ref_is_rejected(self):
        self.assertTrue(problems("- uses: actions/checkout@main"))

    def test_an_unpinned_sample_is_reported_with_file_line_and_action(self):
        (msg,) = problems("- uses: astral-sh/setup-uv@v5")
        self.assertIn("w.yml:4", msg)
        self.assertIn("astral-sh/setup-uv@v5", msg)

    def test_a_short_sha_is_rejected(self):
        self.assertTrue(problems("- uses: actions/checkout@11d5960 # v4"))

    def test_a_sha_without_a_version_comment_is_rejected(self):
        (msg,) = problems(f"- uses: actions/checkout@{SHA}")
        self.assertIn("version comment", msg)

    def test_quoted_and_nested_action_paths_are_checked(self):
        self.assertTrue(problems("- uses: 'github/codeql-action/upload-sarif@v3'"))
        self.assertEqual(
            problems(f'- uses: "github/codeql-action/upload-sarif@{SHA}" # v3.28.0'), []
        )

    def test_reusable_workflow_refs_are_checked(self):
        self.assertTrue(problems("uses: org/repo/.github/workflows/x.yml@v1"))

    def test_local_actions_are_exempt(self):
        self.assertEqual(problems("- uses: ./.github/actions/local"), [])

    def test_docker_references_need_a_digest(self):
        self.assertTrue(problems("- uses: docker://alpine:3.19"))
        digest = "sha256:" + "a" * 64
        self.assertEqual(problems(f"- uses: docker://alpine@{digest} # 3.19"), [])

    def test_a_uses_word_inside_a_run_script_is_ignored(self):
        self.assertEqual(problems("- run: echo uses: actions/checkout@v4"), [])

    def test_every_workflow_in_this_repository_is_pinned(self):
        root = Path(__file__).resolve().parents[2]
        found = pins.check_paths([root / ".github"])
        self.assertEqual(found, [], "\n".join(found))


if __name__ == "__main__":
    unittest.main()
