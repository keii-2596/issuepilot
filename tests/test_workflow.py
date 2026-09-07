import unittest

from issuepilot.workflow import _safe_pr_text, parse_issue_url


class ParseIssueUrlTests(unittest.TestCase):
    def test_parses_normal_issue_url(self):
        self.assertEqual(
            parse_issue_url("https://github.com/octo/example/issues/42"),
            ("octo/example", 42),
        )

    def test_accepts_url_suffix(self):
        self.assertEqual(
            parse_issue_url("https://github.com/octo/example/issues/42#issuecomment-1"),
            ("octo/example", 42),
        )

    def test_rejects_pull_request_url(self):
        with self.assertRaises(ValueError):
            parse_issue_url("https://github.com/octo/example/pull/42")

    def test_pr_text_neutralizes_mentions_and_extra_closing_keywords(self):
        value = _safe_pr_text("@all fixes #999 and resolves #123")
        self.assertIn("@\u200ball", value)
        self.assertIn("`fixes` #999", value)
        self.assertIn("`resolves` #123", value)


if __name__ == "__main__":
    unittest.main()
