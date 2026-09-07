import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issuepilot.codex_agent import CodexAgent, CodexError
from tests.helpers import issue


class CodexAgentTests(unittest.TestCase):
    def test_uses_sandbox_secret_filter_closed_stdin_and_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)

            def fake_run(command, **kwargs):
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "status": "fixed",
                            "summary": "done",
                            "tests": ["passed"],
                            "pr_title": "Fix",
                            "pr_body": "Body",
                            "risk": "low",
                            "workspace_report_zh": "## Issue 中文翻译\n\n完成。",
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertNotIn("--approve-for-me", command)
                self.assertIn("--ignore-rules", command)
                self.assertTrue(
                    any(
                        value.startswith("projects.") and 'trust_level="untrusted"' in value
                        for value in command
                    )
                )
                self.assertIn("shell_environment_policy.ignore_default_excludes=false", command)
                self.assertIn("sandbox_workspace_write.network_access=false", command)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(kwargs["timeout"], 123)
                self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
                self.assertNotIn("GH_TOKEN", kwargs["env"])
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.dict(
                os.environ,
                {"GITHUB_TOKEN": "secret-one", "GH_TOKEN": "secret-two"},
            ):
                with patch("issuepilot.codex_agent.subprocess.run", side_effect=fake_run):
                    result = CodexAgent("codex", timeout_seconds=123).solve(
                        issue(), repository
                    )
            self.assertEqual(result.status, "fixed")

    def test_reports_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "issuepilot.codex_agent.subprocess.run",
                side_effect=subprocess.TimeoutExpired("codex", 60),
            ):
                with self.assertRaisesRegex(CodexError, "timed out after 60 seconds"):
                    CodexAgent("codex", timeout_seconds=60).solve(
                        issue(), Path(directory)
                    )


if __name__ == "__main__":
    unittest.main()
