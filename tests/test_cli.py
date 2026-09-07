import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from issuepilot.cli import main
from issuepilot.github import GitHubRateLimitError


class CliErrorTests(unittest.TestCase):
    def test_json_mode_exposes_rate_limit_error_code(self):
        config = SimpleNamespace(github_token=None)
        stderr = io.StringIO()

        with patch("issuepilot.cli.Config.load", return_value=config):
            with patch("issuepilot.cli._client", return_value=object()):
                with patch(
                    "issuepilot.cli._find",
                    side_effect=GitHubRateLimitError("GitHub API 403: rate limit"),
                ):
                    with redirect_stderr(stderr):
                        exit_code = main(["--json", "discover", "--limit", "10"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            json.loads(stderr.getvalue())["error_code"], "github_rate_limited"
        )

    def test_publish_passes_one_shot_confirmation_to_workflow(self):
        issue_url = "https://github.com/octo/example/issues/42"
        config = SimpleNamespace(github_token="fake-token")
        client = object()
        outcome = SimpleNamespace(
            status="published",
            issue=SimpleNamespace(html_url=issue_url),
            message="Draft pull request created",
            workspace=None,
            pr_url="https://github.com/octo/example/pull/99",
        )
        stdout = io.StringIO()

        with patch("issuepilot.cli.Config.load", return_value=config):
            with patch("issuepilot.cli._client", return_value=client):
                with patch(
                    "issuepilot.cli.publish_prepared", return_value=outcome
                ) as publish:
                    with redirect_stdout(stdout):
                        exit_code = main(
                            [
                                "--json",
                                "publish",
                                "--issue",
                                issue_url,
                                "--confirm-current-diff",
                            ]
                        )

        self.assertEqual(exit_code, 0)
        publish.assert_called_once_with(
            client,
            config,
            issue_url,
            publish_confirmed=True,
        )
        self.assertEqual(json.loads(stdout.getvalue())["status"], "published")


if __name__ == "__main__":
    unittest.main()
