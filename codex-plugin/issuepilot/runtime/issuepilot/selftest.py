import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

from .codex_agent import CodexAgent
from .config import Config
from .github import run_git
from .models import Issue, Repository
from .policy import inspect_changes


def run_selftest(config: Config) -> Dict[str, Any]:
    """Exercise the real Codex adapter against a disposable trusted repository."""
    if not config.codex_path:
        raise RuntimeError("Codex CLI was not found")
    with tempfile.TemporaryDirectory(prefix="issuepilot-selftest-") as directory:
        repository_path = Path(directory) / "repository"
        repository_path.mkdir()
        run_git(["init", "-q"], repository_path)
        run_git(["checkout", "-qb", "main"], repository_path)
        run_git(["config", "user.name", "IssuePilot Self-test"], repository_path)
        run_git(
            ["config", "user.email", "selftest@issuepilot.invalid"], repository_path
        )
        (repository_path / "calculator.py").write_text(
            "def add(left, right):\n    return left - right\n", encoding="utf-8"
        )
        (repository_path / "test_calculator.py").write_text(
            "import unittest\n\n"
            "from calculator import add\n\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_adds_two_numbers(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        run_git(
            ["add", "--", "calculator.py", "test_calculator.py"], repository_path
        )
        run_git(["commit", "-qm", "self-test fixture"], repository_path)
        repository = Repository(
            full_name="issuepilot/selftest",
            stars=10000,
            default_branch="main",
            clone_url="https://github.com/issuepilot/selftest.git",
            html_url="https://github.com/issuepilot/selftest",
            language="Python",
        )
        issue = Issue(
            repo=repository,
            number=1,
            title="add() subtracts instead of adding",
            body=(
                "The add(left, right) function returns left - right. Change it to "
                "return the arithmetic sum and verify the existing regression test."
            ),
            html_url="https://github.com/issuepilot/selftest/issues/1",
            labels=["good first issue", "bug"],
            state="open",
        )
        original_head = run_git(["rev-parse", "HEAD"], repository_path).stdout.strip()
        result = CodexAgent(
            config.codex_path,
            config.codex_model,
            config.codex_timeout_seconds,
        ).solve(issue, repository_path)
        if run_git(["rev-parse", "HEAD"], repository_path).stdout.strip() != original_head:
            raise RuntimeError("Self-test failed: Codex changed Git history")
        if result.status != "fixed":
            raise RuntimeError(
                "Self-test failed: Codex returned {} ({})".format(
                    result.status, result.summary
                )
            )
        policy = inspect_changes(
            repository_path, config.max_changed_files, config.max_diff_bytes
        )
        if not policy.allowed:
            raise RuntimeError(
                "Self-test failed policy checks: {}".format("; ".join(policy.reasons))
            )
        verification = subprocess.run(
            [sys.executable, "-m", "unittest", "-q"],
            cwd=str(repository_path),
            text=True,
            capture_output=True,
            check=False,
        )
        if verification.returncode != 0:
            detail = verification.stderr.strip() or verification.stdout.strip()
            raise RuntimeError("Self-test verification failed: {}".format(detail))
        return {
            "status": "passed",
            "agent_status": result.status,
            "summary": result.summary,
            "agent_reported_tests": result.tests,
            "changed_files": policy.changed_files,
            "independent_verification": "python -m unittest -q: passed",
        }
