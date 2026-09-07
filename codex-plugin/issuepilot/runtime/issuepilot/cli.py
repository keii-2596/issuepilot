import argparse
import json
import subprocess
import sys
from typing import List, Optional

from .config import DISCOVERY_SORT_MODES, Config
from .discovery import discover
from .github import GitHubClient, GitHubError, GitHubRateLimitError
from .models import Issue, RunOutcome
from .interests import DOMAIN_PRESETS, normalize_interests
from .selftest import run_selftest
from .state import (
    advance_discovery_page,
    archive_run,
    discovery_page,
    list_manifests,
    save_discovery_run,
)
from .workflow import parse_issue_url, publish_prepared, run_issue


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issuepilot",
        description="Find high-signal GitHub issues and let Codex prepare draft fixes.",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check local authentication and tools")
    commands.add_parser("selftest", help="run Codex on a disposable local fixture")
    discovery = commands.add_parser("discover", help="rank candidate issues")
    discovery.add_argument("--limit", type=int, default=20)
    discovery.add_argument("--min-stars", type=int)
    discovery.add_argument("--max-stars", type=int)
    discovery.add_argument("--language", action="append", dest="languages")
    discovery.add_argument("--label", action="append", dest="labels")
    discovery.add_argument("--domain", action="append", choices=list(DOMAIN_PRESETS))
    discovery.add_argument("--keyword", action="append")
    discovery.add_argument("--exclude-keyword", action="append")
    discovery.add_argument("--all-domains", action="store_true", help="ignore saved domain preferences")
    discovery.add_argument("--updated-within-days", type=int)
    discovery.add_argument("--created-within-days", type=int)
    discovery.add_argument("--repo-active-within-days", type=int)
    discovery.add_argument("--sort", choices=DISCOVERY_SORT_MODES, dest="sort_mode")
    one = commands.add_parser("run", help="analyze and fix one issue")
    one.add_argument("--issue", required=True, help="full GitHub issue URL")
    one.add_argument("--publish", action="store_true", help="push and open a draft PR")
    publish = commands.add_parser("publish", help="publish an existing prepared fix")
    publish.add_argument("--issue", required=True, help="full GitHub issue URL")
    publish.add_argument(
        "--confirm-current-diff",
        action="store_true",
        help=(
            "one-shot confirmation that the user explicitly approved publishing "
            "the currently reviewed diff as a draft PR"
        ),
    )
    commands.add_parser("prepared", help="list prepared and published runs")
    web = commands.add_parser("web", help="open the local web dashboard")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-open", action="store_true", help="do not open a browser")
    archive = commands.add_parser("archive", help="archive a workspace so it can be retried")
    archive.add_argument("--issue", required=True, help="full GitHub issue URL")
    auto = commands.add_parser("autopilot", help="try top-ranked issues")
    auto.add_argument("--max-issues", type=int, default=1)
    auto.add_argument("--publish", action="store_true", help="push successful fixes as draft PRs")
    return parser


def _client(config: Config, require_auth: bool = False) -> GitHubClient:
    if require_auth and not config.github_token:
        raise RuntimeError(
            "GitHub login is required for publishing. Set GITHUB_TOKEN/GH_TOKEN, "
            "or install and sign in with gh."
        )
    return GitHubClient(config.github_token)


def _candidate_dict(issue: Issue) -> dict:
    return {
        "score": issue.score,
        "stars": issue.repo.stars,
        "language": issue.repo.language,
        "repository": issue.repo.full_name,
        "repository_description": issue.repo.description,
        "repository_topics": issue.repo.topics,
        "issue": issue.number,
        "title": issue.title,
        "url": issue.html_url,
        "labels": issue.labels,
        "fit": issue.fit,
        "fit_reasons": issue.fit_reasons,
        "risk_reasons": issue.risk_reasons,
        "longevity_reason": issue.longevity_reason,
        "age_days": issue.age_days,
        "updated_days": issue.updated_days,
        "verification": issue.verification,
        "warnings": issue.warnings,
    }


def _outcome_dict(outcome: RunOutcome) -> dict:
    return {
        "status": outcome.status,
        "issue": outcome.issue.html_url,
        "message": outcome.message,
        "workspace": str(outcome.workspace) if outcome.workspace else None,
        "pull_request": outcome.pr_url,
    }


def _find(
    config: Config,
    client: GitHubClient,
    limit: int,
    min_stars: Optional[int] = None,
    languages: Optional[List[str]] = None,
    labels: Optional[List[str]] = None,
    max_issue_idle_days: Optional[int] = None,
    max_repo_idle_days: Optional[int] = None,
    max_stars: Optional[int] = None,
    max_issue_age_days: Optional[int] = None,
    sort_mode: Optional[str] = None,
    interests: Optional[dict] = None,
) -> List[Issue]:
    interests = normalize_interests((getattr(config, "interests", None) if interests is None else interests) or {})
    start_page = discovery_page(config)
    metrics = {}
    candidates = discover(
        client,
        min_stars if min_stars is not None else config.min_stars,
        languages or config.languages,
        config.repo_limit,
        config.issues_per_repo,
        limit,
        max_repo_idle_days if max_repo_idle_days is not None else config.max_repo_idle_days,
        config.excluded_topics,
        max_issue_idle_days if max_issue_idle_days is not None else config.max_issue_idle_days,
        labels or config.issue_labels,
        config.active_work_days,
        start_page=start_page,
        max_scan_pages=config.discovery_scan_pages,
        concurrency=config.discovery_concurrency,
        metrics=metrics,
        max_stars=max_stars if max_stars is not None else config.max_stars,
        max_issue_age_days=(
            max_issue_age_days
            if max_issue_age_days is not None
            else config.max_issue_age_days
        ),
        sort_mode=sort_mode or config.discovery_sort,
        interests=interests,
    )
    advance_discovery_page(
        config,
        start_page,
        int(metrics.get("pages_scanned", 1)),
        int(metrics.get("search_page_limit", 250)),
    )
    save_discovery_run(
        config,
        candidates,
        {
            "min_stars": min_stars if min_stars is not None else config.min_stars,
            "max_stars": max_stars if max_stars is not None else config.max_stars,
            "languages": languages or config.languages,
            "repo_limit": config.repo_limit,
            "issues_per_repo": config.issues_per_repo,
            "max_repo_idle_days": max_repo_idle_days if max_repo_idle_days is not None else config.max_repo_idle_days,
            "max_issue_idle_days": max_issue_idle_days if max_issue_idle_days is not None else config.max_issue_idle_days,
            "max_issue_age_days": max_issue_age_days if max_issue_age_days is not None else config.max_issue_age_days,
            "issue_labels": labels or config.issue_labels,
            "active_work_days": config.active_work_days,
            "sort_mode": sort_mode or config.discovery_sort,
            "limit": limit,
            "interests": interests,
        },
        metrics,
    )
    return candidates


def _codex_logged_in(executable: Optional[str]) -> bool:
    if not executable:
        return False
    result = subprocess.run(
        [executable, "login", "status"], text=True, capture_output=True, check=False
    )
    return result.returncode == 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = Config.load()
        if args.command == "doctor":
            codex_authenticated = _codex_logged_in(config.codex_path)
            data = {
                "github_authenticated": bool(config.github_token),
                "github_mode": "authenticated" if config.github_token else "anonymous-read-only",
                "codex_found": bool(config.codex_path),
                "codex_authenticated": codex_authenticated,
                "codex_path": config.codex_path,
                "publish_unlocked": config.allow_publish,
                "workspace": str(config.workspace_root),
            }
            if config.github_token:
                data["github_user"] = _client(config, require_auth=True).viewer()
            data["read_only_ready"] = codex_authenticated
            data["publish_ready"] = bool(config.github_token) and codex_authenticated
            _print(data, args.json)
            return 0 if data["read_only_ready"] else 2

        if args.command == "selftest":
            _print(run_selftest(config), args.json)
            return 0

        if args.command == "prepared":
            _print(list_manifests(config), args.json)
            return 0

        if args.command == "web":
            from .web import serve

            serve(args.host, args.port, not args.no_open)
            return 0

        if args.command == "archive":
            full_name, number = parse_issue_url(args.issue)
            destination = archive_run(config, full_name, number)
            _print(
                {
                    "status": "archived",
                    "issue": args.issue,
                    "archive": str(destination),
                },
                args.json,
            )
            return 0

        requires_auth = args.command == "publish" or bool(
            getattr(args, "publish", False)
        )
        client = _client(config, require_auth=requires_auth)
        if args.command == "discover":
            if args.limit < 1 or args.limit > 100:
                raise ValueError("--limit must be between 1 and 100")
            for name in (
                "min_stars",
                "max_stars",
                "updated_within_days",
                "created_within_days",
                "repo_active_within_days",
            ):
                value = getattr(args, name)
                if value is not None and value < 1:
                    raise ValueError("--{} must be at least 1".format(name.replace("_", "-")))
            minimum_stars = (
                args.min_stars
                if args.min_stars is not None
                else getattr(config, "min_stars", 1)
            )
            maximum_stars = (
                args.max_stars
                if args.max_stars is not None
                else getattr(config, "max_stars", 1_000_000)
            )
            if maximum_stars < minimum_stars:
                raise ValueError("--max-stars must be at least --min-stars")
            candidates = [
                _candidate_dict(item)
                for item in _find(
                    config,
                    client,
                    args.limit,
                    args.min_stars,
                    args.languages,
                    args.labels,
                    args.updated_within_days,
                    args.repo_active_within_days,
                    args.max_stars,
                    args.created_within_days,
                    args.sort_mode,
                    ({} if args.all_domains else normalize_interests({
                        "domains": args.domain or [],
                        "keywords": args.keyword or [],
                        "excluded_keywords": args.exclude_keyword or [],
                    }) if any((args.domain, args.keyword, args.exclude_keyword)) else getattr(config, "interests", None)),
                )
            ]
            _print(candidates, args.json)
            return 0
        if args.command == "run":
            repo, number = parse_issue_url(args.issue)
            outcome = run_issue(client, config, client.get_issue(repo, number), args.publish)
            _print(_outcome_dict(outcome), args.json)
            return 0 if outcome.status in ("prepared", "published") else 3
        if args.command == "publish":
            outcome = publish_prepared(
                client,
                config,
                args.issue,
                publish_confirmed=args.confirm_current_diff,
            )
            _print(_outcome_dict(outcome), args.json)
            return 0 if outcome.status == "published" else 3
        if args.command == "autopilot":
            if args.max_issues < 1 or args.max_issues > 10:
                raise ValueError("--max-issues must be between 1 and 10")
            outcomes = []
            for issue in _find(config, client, args.max_issues):
                try:
                    outcome = run_issue(client, config, issue, args.publish)
                    outcomes.append(_outcome_dict(outcome))
                except (GitHubError, RuntimeError, ValueError) as exc:
                    outcomes.append(
                        {
                            "status": "error",
                            "issue": issue.html_url,
                            "message": str(exc),
                            "workspace": None,
                            "pull_request": None,
                        }
                    )
            _print(outcomes, args.json)
            return 0 if any(item["status"] in ("prepared", "published") for item in outcomes) else 3
        return 1
    except (GitHubError, RuntimeError, ValueError, OSError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error_code": (
                            "github_rate_limited"
                            if isinstance(exc, GitHubRateLimitError)
                            else "operation_failed"
                        ),
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print("error: {}".format(exc), file=sys.stderr)
        return 2


def _print(value: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if isinstance(value, list):
        if not value:
            print("No matching issues found.")
        for item in value:
            if isinstance(item, dict) and "repository" in item:
                print(
                    "[{score:>6}] ★{stars:<8} {repository}#{issue}  {title}\n          {url}".format(**item)
                )
            else:
                print(json.dumps(item, ensure_ascii=False, indent=2))
    elif isinstance(value, dict):
        for key, item in value.items():
            print("{}: {}".format(key, item))
    else:
        print(value)


if __name__ == "__main__":
    raise SystemExit(main())
