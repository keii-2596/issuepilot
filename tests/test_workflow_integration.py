import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from issuepilot.config import Config
from issuepilot.models import AgentResult, Issue, IssueActivity, Repository
from issuepilot import workflow
from issuepilot.workflow import publish_prepared, run_issue


def git(cwd, *args):
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd), text=True, capture_output=True, check=True
    )


class FakeAgent:
    def solve(self, issue, repository):
        (repository / "app.py").write_text(
            "def value():\n    return 2\n", encoding="utf-8"
        )
        (repository / "test_app.py").write_text(
            "from app import value\n\ndef test_value():\n    assert value() == 2\n",
            encoding="utf-8",
        )
        return AgentResult(
            status="fixed",
            summary="Return the corrected value and cover it with a test.",
            tests=["python -m unittest: passed"],
            pr_title="Fix returned value",
            pr_body="Correct the returned value and add a regression test.",
            risk="low",
            workspace_report_zh=(
                "## Issue 中文翻译\n\n修复返回值。\n\n"
                "## 全部讨论中文翻译\n\n暂无评论。\n\n"
                "## Issue 分析\n\n返回值错误。\n\n"
                "## 实际改动\n\n修改 app.py 并添加测试。\n\n"
                "## 验证情况\n\n测试通过。"
            ),
        )


class FakeNeedsHumanAgent:
    def solve(self, issue, repository):
        (repository / "app.py").write_text(
            "def value():\n    return 2\n", encoding="utf-8"
        )
        return AgentResult(
            status="needs_human",
            summary="A dependency is required before validation can finish.",
            tests=["validation blocked: dependency unavailable"],
            pr_title="",
            pr_body="",
            risk="medium",
        )


class FakeClient:
    def __init__(self, issue):
        self.issue = issue
        self.existing_pr = None
        self.linked_pr = None
        self.recent_commit = None
        self.active_claim = None
        self.likely_resolved = None
        self.pull_requests = []
        self.forks = []

    def get_issue(self, full_name, number):
        self.assert_issue(full_name, number)
        return self.issue

    def assert_issue(self, full_name, number):
        if full_name != self.issue.repo.full_name or number != self.issue.number:
            raise AssertionError("unexpected issue")

    def viewer_identity(self):
        return "tester", "123+tester@users.noreply.github.com"

    def find_open_pull_request(self, repo, head):
        return self.existing_pr

    def find_open_linked_pull_request(self, issue):
        return self.linked_pr

    def inspect_issue_activity(self, issue, recent_days):
        return IssueActivity(
            open_pr_url=self.linked_pr,
            recent_commit_url=self.recent_commit,
            active_claim_url=self.active_claim,
            likely_resolved_url=self.likely_resolved,
        )

    def fork(self, repo, owner):
        self.forks.append((repo.full_name, owner))
        return "tester/example"

    def create_pull_request(self, repo, head, title, body, draft=True):
        value = {
            "repo": repo.full_name,
            "head": head,
            "title": title,
            "body": body,
            "draft": draft,
        }
        self.pull_requests.append(value)
        self.existing_pr = "https://github.com/octo/example/pull/99"
        return self.existing_pr


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "source"
        source.mkdir()
        git(source, "init", "-q")
        git(source, "checkout", "-qb", "main")
        git(source, "config", "user.name", "Fixture")
        git(source, "config", "user.email", "fixture@example.com")
        (source / "app.py").write_text(
            "def value():\n    return 1\n", encoding="utf-8"
        )
        git(source, "add", "--", "app.py")
        git(source, "commit", "-qm", "initial")
        origin = self.root / "origin.git"
        git(self.root, "clone", "-q", "--bare", str(source), str(origin))
        repo = Repository(
            full_name="octo/example",
            stars=10000,
            default_branch="main",
            clone_url=str(origin),
            html_url="https://github.com/octo/example",
            language="Python",
        )
        self.issue = Issue(
            repo=repo,
            number=42,
            title="Return the correct value",
            body="value() should return 2.",
            html_url="https://github.com/octo/example/issues/42",
            labels=["good first issue"],
            state="open",
            updated_at="2026-08-20T00:00:00Z",
        )
        self.client = FakeClient(self.issue)

    def tearDown(self):
        self.temp.cleanup()

    def config(self, allow_publish=False):
        return Config(
            github_token="fake-token",
            codex_path="fake-codex",
            codex_model=None,
            workspace_root=self.root / "state" / "workspaces",
            min_stars=5000,
            languages=["Python"],
            repo_limit=5,
            issues_per_repo=5,
            max_changed_files=20,
            max_diff_bytes=200000,
            allow_publish=allow_publish,
        )

    def prepare(self):
        with patch("issuepilot.workflow.CodexAgent", return_value=FakeAgent()):
            return run_issue(self.client, self.config(), self.issue, publish=False)

    def test_prepare_writes_manifest_and_keeps_changes_uncommitted(self):
        outcome = self.prepare()
        self.assertEqual(outcome.status, "prepared")
        self.assertIn("issuepilot publish", outcome.message)
        status = git(outcome.workspace, "status", "--porcelain").stdout
        self.assertIn("app.py", status)
        self.assertIn("test_app.py", status)
        manifests = list((self.root / "state" / "manifests").glob("*.json"))
        self.assertEqual(len(manifests), 1)
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "prepared")
        self.assertIsNone(data["commit_sha"])
        review = outcome.workspace / workflow.WORKSPACE_REPORT_NAME
        self.assertTrue(review.is_file())
        self.assertIn("Issue 中文翻译", review.read_text(encoding="utf-8"))
        self.assertNotIn(workflow.WORKSPACE_REPORT_NAME, status)
        excluded = git(
            outcome.workspace,
            "check-ignore",
            "--no-index",
            workflow.WORKSPACE_REPORT_NAME,
        ).stdout.strip()
        self.assertEqual(excluded, workflow.WORKSPACE_REPORT_NAME)

    def test_prepare_retries_a_transient_clone_failure(self):
        original_run_git = workflow.run_git
        clone_attempts = {"count": 0}

        def flaky_run_git(args, cwd, check=True):
            if args[0] == "clone":
                clone_attempts["count"] += 1
                if clone_attempts["count"] == 1:
                    return subprocess.CompletedProcess(
                        ["git"] + args,
                        128,
                        "",
                        "error: RPC failed; curl 28 Failed to connect to github.com port 443",
                    )
            return original_run_git(args, cwd, check=check)

        with patch("issuepilot.workflow.github_cli_path", return_value=None):
            with patch("issuepilot.workflow.run_git", side_effect=flaky_run_git):
                with patch("issuepilot.workflow.time.sleep") as pause:
                    with patch(
                        "issuepilot.workflow.CodexAgent", return_value=FakeAgent()
                    ):
                        outcome = run_issue(
                            self.client, self.config(), self.issue, publish=False
                        )

        self.assertEqual(outcome.status, "prepared")
        self.assertEqual(clone_attempts["count"], 2)
        pause.assert_called_once_with(1.0)

    def test_dirty_needs_human_workspace_is_not_called_prepared(self):
        with patch(
            "issuepilot.workflow.CodexAgent", return_value=FakeNeedsHumanAgent()
        ):
            outcome = run_issue(self.client, self.config(), self.issue, publish=False)

        self.assertEqual(outcome.status, "needs_human")
        self.assertEqual(
            list((self.root / "state" / "manifests").glob("*.json")), []
        )
        with self.assertRaisesRegex(
            RuntimeError, "unrecorded local changes.*no prepared manifest"
        ) as raised:
            run_issue(self.client, self.config(), self.issue, publish=False)
        self.assertNotIn("issuepilot publish", str(raised.exception))

    def test_prepare_falls_back_to_github_cli_after_git_transport_failure(self):
        original_run_git = workflow.run_git
        clone_attempts = {"git": 0, "gh": 0}

        def failed_git_clone(args, cwd, check=True):
            if args[0] == "clone":
                clone_attempts["git"] += 1
                return subprocess.CompletedProcess(
                    ["git"] + args,
                    128,
                    "",
                    "fatal: Failed to connect to github.com port 443",
                )
            return original_run_git(args, cwd, check=check)

        def successful_gh_clone(args, cwd, **kwargs):
            clone_attempts["gh"] += 1
            checkout = Path(args[4])
            return original_run_git(
                [
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    self.issue.repo.default_branch,
                    self.issue.repo.clone_url,
                    str(checkout),
                ],
                Path(cwd),
                check=False,
            )

        with patch("issuepilot.workflow.github_cli_path", return_value="/usr/bin/gh"):
            with patch("issuepilot.workflow.run_git", side_effect=failed_git_clone):
                with patch("issuepilot.workflow.run_process", side_effect=successful_gh_clone):
                    with patch("issuepilot.workflow.time.sleep") as pause:
                        with patch(
                            "issuepilot.workflow.CodexAgent", return_value=FakeAgent()
                        ):
                            outcome = run_issue(
                                self.client, self.config(), self.issue, publish=False
                            )

        self.assertEqual(outcome.status, "prepared")
        self.assertEqual(clone_attempts, {"git": 1, "gh": 1})
        pause.assert_not_called()

    def test_prepare_skips_issue_with_someone_elses_open_linked_pr(self):
        self.client.linked_pr = "https://github.com/octo/example/pull/77"
        with patch("issuepilot.workflow.CodexAgent") as agent:
            outcome = run_issue(self.client, self.config(), self.issue, publish=False)
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.pr_url, self.client.linked_pr)
        self.assertIn("open linked pull request", outcome.message)
        agent.assert_not_called()

    def test_prepare_skips_issue_with_recent_linked_commit(self):
        self.client.recent_commit = "https://github.com/octo/example/commit/abc123"
        with patch("issuepilot.workflow.CodexAgent") as agent:
            outcome = run_issue(self.client, self.config(), self.issue, publish=False)
        self.assertEqual(outcome.status, "skipped")
        self.assertIn("recent linked commit", outcome.message)
        self.assertEqual(outcome.pr_url, self.client.recent_commit)
        agent.assert_not_called()

    def test_prepare_skips_issue_reported_as_already_implemented(self):
        self.client.likely_resolved = (
            "https://github.com/octo/example/issues/42#issuecomment-3"
        )
        with patch("issuepilot.workflow.CodexAgent") as agent:
            outcome = run_issue(self.client, self.config(), self.issue, publish=False)
        self.assertEqual(outcome.status, "skipped")
        self.assertIn("already be implemented", outcome.message)
        self.assertEqual(outcome.pr_url, self.client.likely_resolved)
        agent.assert_not_called()

    def test_publish_commits_exact_diff_and_creates_draft_pr(self):
        prepared = self.prepare()
        pushed = []
        with patch(
            "issuepilot.workflow.push_with_token",
            side_effect=lambda path, remote, branch, token: pushed.append(
                (path, remote, branch, token)
            ),
        ):
            outcome = publish_prepared(
                self.client, self.config(allow_publish=True), self.issue.html_url
            )
        self.assertEqual(outcome.status, "published")
        self.assertEqual(outcome.pr_url, "https://github.com/octo/example/pull/99")
        self.assertEqual(len(pushed), 1)
        self.assertEqual(len(self.client.pull_requests), 1)
        self.assertTrue(self.client.pull_requests[0]["draft"])
        self.assertEqual(self.client.pull_requests[0]["head"], "tester:issuepilot/issue-42-return-the-correct-value")
        self.assertEqual(git(prepared.workspace, "status", "--porcelain").stdout, "")
        subject = git(prepared.workspace, "log", "-1", "--pretty=%s").stdout.strip()
        self.assertEqual(subject, "fix: Return the correct value (#42)")

    def test_publish_accepts_one_shot_confirmation_without_persistent_unlock(self):
        prepared = self.prepare()
        with patch("issuepilot.workflow.push_with_token"):
            outcome = publish_prepared(
                self.client,
                self.config(allow_publish=False),
                self.issue.html_url,
                publish_confirmed=True,
            )
        self.assertEqual(outcome.status, "published")
        self.assertEqual(outcome.pr_url, "https://github.com/octo/example/pull/99")
        self.assertTrue(self.client.pull_requests[0]["draft"])
        self.assertEqual(git(prepared.workspace, "status", "--porcelain").stdout, "")

    def test_publish_remains_locked_without_confirmation(self):
        self.prepare()
        with patch("issuepilot.workflow.push_with_token") as push:
            with self.assertRaisesRegex(RuntimeError, "Publishing is locked"):
                publish_prepared(
                    self.client,
                    self.config(allow_publish=False),
                    self.issue.html_url,
                )
        push.assert_not_called()

    def test_publish_blocks_if_reviewed_diff_was_edited(self):
        prepared = self.prepare()
        (prepared.workspace / "app.py").write_text(
            "def value():\n    return 999\n", encoding="utf-8"
        )
        with patch("issuepilot.workflow.push_with_token") as push:
            outcome = publish_prepared(
                self.client, self.config(allow_publish=True), self.issue.html_url
            )
        self.assertEqual(outcome.status, "blocked")
        self.assertIn("diff changed", outcome.message)
        push.assert_not_called()

    def test_publish_skips_when_another_pr_appears_after_preparation(self):
        self.prepare()
        self.client.linked_pr = "https://github.com/octo/example/pull/77"
        with patch("issuepilot.workflow.push_with_token") as push:
            outcome = publish_prepared(
                self.client, self.config(allow_publish=True), self.issue.html_url
            )
        self.assertEqual(outcome.status, "skipped")
        self.assertEqual(outcome.pr_url, self.client.linked_pr)
        push.assert_not_called()

    def test_publish_retry_reuses_commit_after_pr_creation_failure(self):
        prepared = self.prepare()
        original_create = self.client.create_pull_request
        calls = {"count": 0}

        def flaky_create(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary PR failure")
            return original_create(*args, **kwargs)

        self.client.create_pull_request = flaky_create
        with patch("issuepilot.workflow.push_with_token"):
            with self.assertRaisesRegex(RuntimeError, "temporary PR failure"):
                publish_prepared(
                    self.client, self.config(allow_publish=True), self.issue.html_url
                )
            first_commit = git(prepared.workspace, "rev-parse", "HEAD").stdout.strip()
            outcome = publish_prepared(
                self.client, self.config(allow_publish=True), self.issue.html_url
            )
            second_commit = git(prepared.workspace, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(outcome.status, "published")
        self.assertEqual(first_commit, second_commit)
        self.assertEqual(calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
