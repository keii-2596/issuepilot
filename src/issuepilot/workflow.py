import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from subprocess import TimeoutExpired, run as run_process
from typing import Any, Dict

from .codex_agent import CodexAgent
from .config import Config, github_cli_path
from .discovery import score_issue
from .github import GitHubClient, push_with_token, run_git
from .models import Issue, RunOutcome
from .policy import change_fingerprint, inspect_changes
from .state import (
    create_manifest,
    load_manifest,
    manifest_path,
    result_from_manifest,
    save_manifest,
)


ISSUE_URL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/issues/(\d+)(?:[/?#].*)?$")
CLONE_RETRY_DELAYS = (1.0, 3.0)
RETRYABLE_CLONE_ERRORS = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection reset",
    "network is unreachable",
    "operation timed out",
    "rpc failed",
    "early eof",
    "http/2 stream",
    "remote end hung up",
    "tls",
    "ssl",
    "expected flush after ref listing",
)
WORKSPACE_REPORT_NAME = "ISSUEPILOT_REVIEW.zh-CN.md"


def parse_issue_url(url: str) -> tuple:
    match = ISSUE_URL.match(url.strip())
    if not match:
        raise ValueError("Expected a URL like https://github.com/owner/repo/issues/123")
    return match.group(1), int(match.group(2))


def _slug(value: str, limit: int = 36) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:limit].rstrip("-") or "fix"


def _workspace(config: Config, issue: Issue) -> Path:
    owner, name = issue.repo.full_name.split("/", 1)
    return config.workspace_root / "{}--{}--issue-{}".format(owner, name, issue.number)


def _branch(issue: Issue) -> str:
    return "issuepilot/issue-{}-{}".format(issue.number, _slug(issue.title))


def _git_config_hash(path: Path) -> str:
    return hashlib.sha256((path / ".git" / "config").read_bytes()).hexdigest()


def _active_hooks(path: Path) -> list:
    hooks = path / ".git" / "hooks"
    if not hooks.is_dir():
        return []
    return sorted(
        item.name
        for item in hooks.iterdir()
        if item.is_file() and not item.name.endswith(".sample")
    )


def _is_retryable_clone_error(detail: str) -> bool:
    lowered = detail.lower()
    return any(pattern in lowered for pattern in RETRYABLE_CLONE_ERRORS)


def _clone_repository(config: Config, issue: Issue, path: Path) -> None:
    attempts = len(CLONE_RETRY_DELAYS) + 1
    last_detail = "unknown git error"
    github_cli_attempted = False
    for attempt in range(attempts):
        with tempfile.TemporaryDirectory(
            prefix=".issuepilot-clone-", dir=str(config.workspace_root)
        ) as temporary_directory:
            checkout = Path(temporary_directory) / "repository"
            result = run_git(
                [
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    issue.repo.default_branch,
                    issue.repo.clone_url,
                    str(checkout),
                ],
                config.workspace_root,
                check=False,
            )
            if result.returncode == 0:
                checkout.replace(path)
                return
            last_detail = result.stderr.strip() or result.stdout.strip()
            gh = github_cli_path()
            if (
                not github_cli_attempted
                and gh
                and _is_retryable_clone_error(last_detail)
            ):
                github_cli_attempted = True
                env = os.environ.copy()
                env.update({"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
                try:
                    fallback = run_process(
                        [
                            gh,
                            "repo",
                            "clone",
                            issue.repo.full_name,
                            str(checkout),
                            "--",
                            "--depth",
                            "1",
                            "--branch",
                            issue.repo.default_branch,
                        ],
                        cwd=str(config.workspace_root),
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=300,
                        env=env,
                    )
                except TimeoutExpired:
                    last_detail = "GitHub CLI clone timed out after 300 seconds"
                else:
                    if fallback.returncode == 0:
                        checkout.replace(path)
                        return
                    fallback_detail = (
                        fallback.stderr.strip()
                        or fallback.stdout.strip()
                        or "unknown GitHub CLI error"
                    )
                    last_detail = "{}; GitHub CLI fallback: {}".format(
                        last_detail, fallback_detail
                    )
        if attempt >= attempts - 1 or not _is_retryable_clone_error(last_detail):
            break
        time.sleep(CLONE_RETRY_DELAYS[attempt])
    attempted = attempt + 1
    suffix = " after {} attempts".format(attempted) if attempted > 1 else ""
    raise RuntimeError("Clone failed{}: {}".format(suffix, last_detail))


def _prepare_checkout(config: Config, issue: Issue) -> tuple:
    path = _workspace(config, issue)
    if path.exists() or path.is_symlink():
        if path.is_symlink():
            raise RuntimeError("Refusing to reuse a symlinked workspace: {}".format(path))
        if not (path / ".git").is_dir():
            raise RuntimeError("Workspace exists but is not a git repository: {}".format(path))
        hooks = _active_hooks(path)
        if hooks:
            raise RuntimeError(
                "Existing workspace has active Git hooks: {}".format(", ".join(hooks))
            )
        status = run_git(["status", "--porcelain"], path).stdout.strip()
        if status:
            if not manifest_path(
                config, issue.repo.full_name, issue.number
            ).is_file():
                raise RuntimeError(
                    "Workspace contains unrecorded local changes and has no "
                    "prepared manifest. Preserve and review it, then archive or "
                    "move it aside before rerunning: {}".format(path)
                )
            raise RuntimeError(
                "Workspace has an existing prepared fix. Inspect it, then use "
                "`issuepilot publish --issue <URL>` or move it aside: {}".format(path)
            )
        remotes = run_git(["remote"], path).stdout.split()
        source_remote = "upstream" if "upstream" in remotes else "origin"
        source_url = run_git(["remote", "get-url", source_remote], path).stdout.strip()
        expected = issue.repo.full_name.lower()
        normalized = source_url.lower().replace(":", "/")
        if expected not in normalized:
            raise RuntimeError(
                "Existing workspace remote does not match {}".format(issue.repo.full_name)
            )
        run_git(["fetch", source_remote, issue.repo.default_branch, "--depth", "1"], path)
        run_git(["checkout", issue.repo.default_branch], path)
        run_git(["reset", "--hard", "FETCH_HEAD"], path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _clone_repository(config, issue, path)
    branch = _branch(issue)
    run_git(["checkout", "-B", branch], path)
    return path, branch


def _check_issue(issue: Issue) -> str:
    if issue.state != "open":
        return "Issue is closed"
    if issue.assignee:
        return "Issue is already assigned to @{}".format(issue.assignee)
    if issue.locked:
        return "Issue is locked"
    return ""


def _matching_pr(client: GitHubClient, issue: Issue, viewer: str) -> str:
    return client.find_open_pull_request(
        issue.repo, "{}:{}".format(viewer, _branch(issue))
    ) or ""


def _activity_blocker(client: GitHubClient, issue: Issue, recent_days: int) -> tuple:
    if not hasattr(client, "inspect_issue_activity"):
        linked_pr = client.find_open_linked_pull_request(issue)
        if linked_pr:
            return "Issue already has an open linked pull request: {}".format(
                linked_pr
            ), linked_pr
        return "", ""
    activity = client.inspect_issue_activity(issue, recent_days)
    if activity.open_pr_url:
        return "Issue already has an open linked pull request: {}".format(
            activity.open_pr_url
        ), activity.open_pr_url
    if activity.recent_commit_url:
        return "Issue has a recent linked commit that may be active work: {}".format(
            activity.recent_commit_url
        ), activity.recent_commit_url
    if activity.active_claim_url:
        return "A contributor recently said they are working on this issue: {}".format(
            activity.active_claim_url
        ), activity.active_claim_url
    if activity.likely_resolved_url:
        return "The issue may already be implemented, fixed, or a duplicate: {}".format(
            activity.likely_resolved_url
        ), activity.likely_resolved_url
    return "", ""


def _safe_pr_text(value: str) -> str:
    value = value.replace("@", "@\u200b")
    closing = re.compile(
        r"(?i)\b(fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+(#\d+)"
    )
    return closing.sub(lambda match: "`{}` {}".format(match.group(1), match.group(2)), value)


def _write_workspace_report(
    path: Path, issue: Issue, result: Any, outcome_status: str
) -> Path:
    """Write a visible local review guide that can never enter the contribution diff."""
    exclude_path = path / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    excluded = {
        line.strip() for line in existing.splitlines() if line.strip() and not line.startswith("#")
    }
    if WORKSPACE_REPORT_NAME not in excluded:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        exclude_path.write_text(
            existing + separator + WORKSPACE_REPORT_NAME + "\n", encoding="utf-8"
        )

    report_body = str(getattr(result, "workspace_report_zh", "") or "").strip()
    if not report_body:
        report_body = (
            "## Issue 中文翻译\n\nAI 未返回翻译，请根据原 Issue 人工复核。\n\n"
            "## 全部讨论中文翻译\n\nAI 未返回讨论翻译，请人工复核。\n\n"
            "## Issue 分析\n\n{}\n\n"
            "## 实际改动\n\n{}\n\n"
            "## 验证情况\n\n{}\n"
        ).format(
            getattr(result, "summary", "暂无"),
            getattr(result, "summary", "暂无"),
            "\n".join("- {}".format(item) for item in getattr(result, "tests", []))
            or "- 未报告验证结果",
        )
    header = (
        "# IssuePilot 审核说明\n\n"
        "> 这是 IssuePilot 生成的本地审核文档，已在本 workspace 中忽略，"
        "不会进入 commit 或 PR。\n\n"
        "- 仓库：`{}`\n"
        "- Issue：[#{}]({})\n"
        "- 本地状态：`{}`\n\n"
    ).format(issue.repo.full_name, issue.number, issue.html_url, outcome_status)
    report_path = path / WORKSPACE_REPORT_NAME
    report_path.write_text(header + report_body + "\n", encoding="utf-8")
    return report_path


def run_issue(
    client: GitHubClient,
    config: Config,
    issue: Issue,
    publish: bool = False,
) -> RunOutcome:
    """Prepare a fix, optionally publishing that exact prepared change."""
    if publish and not config.allow_publish:
        raise RuntimeError(
            "Publishing is locked. Set ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND "
            "after reviewing the safeguards."
        )
    if not config.codex_path:
        raise RuntimeError("Codex CLI was not found")
    issue = client.get_issue(issue.repo.full_name, issue.number)
    ineligible = _check_issue(issue)
    if ineligible:
        return RunOutcome(issue, "skipped", ineligible)
    if publish:
        viewer, _ = client.viewer_identity()
        existing_pr = _matching_pr(client, issue, viewer)
        if existing_pr:
            return RunOutcome(
                issue,
                "published",
                "Matching open pull request already exists",
                pr_url=existing_pr,
            )
    activity_message, activity_url = _activity_blocker(
        client, issue, config.active_work_days
    )
    if activity_message:
        return RunOutcome(
            issue,
            "skipped",
            activity_message,
            pr_url=activity_url,
        )

    issue = replace(issue, score=score_issue(issue))
    path, branch = _prepare_checkout(config, issue)
    original_head = run_git(["rev-parse", "HEAD"], path).stdout.strip()
    original_config_hash = _git_config_hash(path)
    agent = CodexAgent(
        config.codex_path, config.codex_model, config.codex_timeout_seconds
    )
    result = agent.solve(issue, path)
    if run_git(["rev-parse", "HEAD"], path).stdout.strip() != original_head:
        _write_workspace_report(path, issue, result, "blocked")
        return RunOutcome(issue, "blocked", "Agent changed Git history", path)
    if _git_config_hash(path) != original_config_hash:
        _write_workspace_report(path, issue, result, "blocked")
        return RunOutcome(issue, "blocked", "Agent changed local Git configuration", path)
    hooks = _active_hooks(path)
    if hooks:
        _write_workspace_report(path, issue, result, "blocked")
        return RunOutcome(
            issue,
            "blocked",
            "Agent created active Git hooks: {}".format(", ".join(hooks)),
            path,
        )
    if result.status != "fixed":
        _write_workspace_report(path, issue, result, result.status)
        return RunOutcome(issue, result.status, result.summary, path)
    if result.risk not in ("low", "medium"):
        _write_workspace_report(path, issue, result, "needs_human")
        return RunOutcome(
            issue,
            "needs_human",
            "Agent marked the change risk as {}".format(result.risk),
            path,
        )
    policy = inspect_changes(path, config.max_changed_files, config.max_diff_bytes)
    if not policy.allowed:
        _write_workspace_report(path, issue, result, "blocked")
        return RunOutcome(issue, "blocked", "; ".join(policy.reasons), path)

    manifest = create_manifest(
        config,
        issue,
        path,
        branch,
        original_head,
        original_config_hash,
        change_fingerprint(path),
        result,
    )
    _write_workspace_report(path, issue, result, "prepared")
    if publish:
        return _publish_manifest(client, config, issue, manifest)
    return RunOutcome(
        issue,
        "prepared",
        "Fix prepared locally: {}. Tests: {}. Publish this exact diff with "
        "`issuepilot publish --issue {}`.".format(
            result.summary, "; ".join(result.tests) or "not reported", issue.html_url
        ),
        path,
    )


def publish_prepared(
    client: GitHubClient,
    config: Config,
    issue_url: str,
    publish_confirmed: bool = False,
) -> RunOutcome:
    """Publish a previously prepared and fingerprinted fix without rerunning AI."""
    if not config.allow_publish and not publish_confirmed:
        raise RuntimeError(
            "Publishing is locked. After reviewing the exact prepared diff, either "
            "set ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND or repeat this command with "
            "--confirm-current-diff in direct response to the user's explicit "
            "publish request."
        )
    full_name, number = parse_issue_url(issue_url)
    manifest = load_manifest(config, full_name, number)
    issue = client.get_issue(full_name, number)
    ineligible = _check_issue(issue)
    if ineligible:
        return RunOutcome(issue, "skipped", ineligible, Path(manifest["workspace"]))
    return _publish_manifest(client, config, issue, manifest)


def _publish_manifest(
    client: GitHubClient,
    config: Config,
    issue: Issue,
    manifest: Dict[str, Any],
) -> RunOutcome:
    result = result_from_manifest(manifest)
    path = Path(str(manifest["workspace"])).resolve()
    expected_path = _workspace(config, issue).resolve()
    if path != expected_path or not (path / ".git").is_dir():
        raise RuntimeError("Prepared workspace is missing or does not match the issue")
    branch = str(manifest["branch"])
    if branch != _branch(issue):
        raise RuntimeError("Prepared branch does not match the issue")
    hooks = _active_hooks(path)
    if hooks:
        return RunOutcome(
            issue,
            "blocked",
            "Active Git hooks require human review: {}".format(", ".join(hooks)),
            path,
        )

    viewer, viewer_email = client.viewer_identity()
    existing_pr = _matching_pr(client, issue, viewer)
    if existing_pr:
        manifest.update({"status": "published", "pull_request": existing_pr})
        save_manifest(config, manifest)
        return RunOutcome(
            issue,
            "published",
            "Matching open pull request already exists",
            path,
            existing_pr,
        )
    activity_message, activity_url = _activity_blocker(
        client, issue, config.active_work_days
    )
    if activity_message:
        return RunOutcome(
            issue,
            "skipped",
            activity_message,
            path,
            activity_url,
        )

    current_head = run_git(["rev-parse", "HEAD"], path).stdout.strip()
    commit_sha = manifest.get("commit_sha")
    if commit_sha:
        if current_head != commit_sha:
            return RunOutcome(issue, "blocked", "Prepared commit is no longer checked out", path)
        if run_git(["status", "--porcelain"], path).stdout.strip():
            return RunOutcome(issue, "blocked", "Prepared commit has additional local changes", path)
    else:
        current_branch = run_git(["branch", "--show-current"], path).stdout.strip()
        if current_branch != branch or current_head != manifest["base_head"]:
            return RunOutcome(issue, "blocked", "Prepared Git branch or history changed", path)
        if _git_config_hash(path) != manifest["git_config_hash"]:
            return RunOutcome(issue, "blocked", "Local Git configuration changed", path)
        policy = inspect_changes(path, config.max_changed_files, config.max_diff_bytes)
        if not policy.allowed:
            return RunOutcome(issue, "blocked", "; ".join(policy.reasons), path)
        if change_fingerprint(path) != manifest["change_fingerprint"]:
            return RunOutcome(
                issue,
                "blocked",
                "Prepared diff changed after AI verification; run the preparation step again",
                path,
            )
        run_git(["add", "--"] + policy.changed_files, path)
        staged = run_git(["diff", "--cached", "--quiet"], path, check=False)
        if staged.returncode == 0:
            return RunOutcome(issue, "blocked", "No staged changes after policy check", path)
        clean_title = re.sub(r"\s+", " ", issue.title).strip()
        commit_message = "fix: {} (#{})".format(clean_title[:65], issue.number)
        run_git(
            [
                "-c",
                "user.name={}".format(viewer),
                "-c",
                "user.email={}".format(viewer_email),
                "commit",
                "--no-verify",
                "-m",
                commit_message,
            ],
            path,
        )
        commit_sha = run_git(["rev-parse", "HEAD"], path).stdout.strip()
        manifest.update({"status": "committed", "commit_sha": commit_sha})
        save_manifest(config, manifest)

    fork_name = client.fork(issue.repo, viewer)
    fork_url = "https://github.com/{}.git".format(fork_name)
    remotes = run_git(["remote"], path).stdout.split()
    if "upstream" not in remotes:
        if "origin" not in remotes:
            raise RuntimeError("Prepared repository has no upstream remote")
        run_git(["remote", "rename", "origin", "upstream"], path)
    remotes = run_git(["remote"], path).stdout.split()
    if "origin" in remotes:
        run_git(["remote", "set-url", "origin", fork_url], path)
    else:
        run_git(["remote", "add", "origin", fork_url], path)
    push_with_token(path, "origin", branch, config.github_token or "")
    manifest["status"] = "pushed"
    save_manifest(config, manifest)

    existing_pr = _matching_pr(client, issue, viewer)
    if existing_pr:
        pr_url = existing_pr
    else:
        test_lines = (
            "\n".join("- {}".format(item[:500]) for item in result.tests)
            or "- Not reported"
        )
        body = (
            "### Summary\n\n{}\n\n{}\n\nFixes #{}\n\n### Verification\n{}\n\n"
            "> Prepared by IssuePilot with Codex. This is a draft and requires human review."
        ).format(
            _safe_pr_text(result.summary.strip()),
            _safe_pr_text(result.pr_body.strip()),
            issue.number,
            _safe_pr_text(test_lines),
        )
        title = re.sub(
            r"\s+",
            " ",
            result.pr_title or "Fix #{}: {}".format(issue.number, issue.title),
        ).strip()[:256]
        title = _safe_pr_text(title)[:256]
        pr_url = client.create_pull_request(
            issue.repo,
            "{}:{}".format(viewer, branch),
            title,
            body,
            draft=True,
        )
    manifest.update({"status": "published", "pull_request": pr_url})
    save_manifest(config, manifest)
    _record(config.workspace_root.parent, issue, branch, pr_url)
    return RunOutcome(issue, "published", "Draft pull request created", path, pr_url)


def _record(root: Path, issue: Issue, branch: str, pr_url: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "issue": issue.html_url,
        "repository": issue.repo.full_name,
        "branch": branch,
        "pull_request": pr_url,
    }
    with (root / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
