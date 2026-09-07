import http.client
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import Issue, IssueActivity, Repository


class GitHubError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubError):
    pass


def _is_rate_limited(status: int, message: object, remaining: Optional[str] = None) -> bool:
    if status == 429:
        return True
    return status == 403 and (
        remaining == "0" or "rate limit" in str(message).lower()
    )


def _pull_request_mentions_issue(item: Dict[str, Any], issue: Issue) -> bool:
    text = "{}\n{}".format(item.get("title") or "", item.get("body") or "")
    number = str(issue.number)
    patterns = (
        r"https://github\.com/{}/issues/{}(?!\d)".format(
            re.escape(issue.repo.full_name), number
        ),
        r"(?<![\w/]){}#{}(?!\d)".format(
            re.escape(issue.repo.full_name), number
        ),
        r"(?<![\w/])#{}(?!\d)".format(number),
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


CLAIM_PATTERNS = (
    r"\bi(?:'m| am|’m) working on (?:this|a fix)\b",
    r"\bi(?:'ll| will) (?:work on|take) this\b",
    r"\bi(?:'d| would|’d) (?:like|love) to "
    r"(?:work on|take|tackle|implement|fix|handle|address|pick up)\b",
    r"\bi(?:'m| am|’m) interested in "
    r"(?:working on|implementing|taking|fixing|handling)\b",
    r"\bi can (?:take|work on|handle|implement|fix) (?:this|it)\b",
    r"\b(?:may|can|could) i be assigned\b",
    r"\bwould it be (?:all right|alright|okay|ok) to assign "
    r"(?:this|it) to me\b",
    r"\bworking on a (?:fix|pull request|pr)\b",
    r"\bplease assign (?:this|it) to me\b",
    r"(?:我来处理|我正在处理|请分配给我)",
)

RESOLVED_PATTERNS = (
    r"\bthis already exists (?:on|in)\b",
    r"\b(?:has already been|is already) "
    r"(?:implemented|fixed|resolved|available|supported)\b",
    r"\b(?:safe|ready) to close\b",
    r"\b(?:can|should) be closed\b",
    r"\bduplicate of (?:https?://\S+|[\w.-]+/[\w.-]+#\d+|#\d+)\b",
    r"(?:已经实现|已经修复|可以关闭|重复问题)",
)


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _is_recent(value: str, cutoff: datetime) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) >= cutoff
    except ValueError:
        return False


class GitHubClient:
    api_url = "https://api.github.com"

    def __init__(
        self, token: Optional[str] = None, timeout: int = 30, max_retries: int = 3
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self._prefer_curl = False
        self.repository_search_exhausted = False
        self._rate_limit_lock = threading.Lock()
        self._rate_limits: Dict[str, tuple[int, float]] = {}

    @staticmethod
    def _rate_resource(url: str) -> Optional[str]:
        path = urllib.parse.urlparse(url).path
        if "/search/" in path:
            return "search"
        if path == "/graphql":
            return "graphql"
        return "core"

    @staticmethod
    def _header_value(headers: Any, name: str) -> Optional[str]:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return str(value) if value is not None else None

    def _record_rate_limit(self, url: str, headers: Any) -> None:
        resource = self._header_value(
            headers, "X-RateLimit-Resource"
        ) or self._rate_resource(url)
        if resource not in ("search", "core", "graphql"):
            return
        try:
            remaining = int(self._header_value(headers, "X-RateLimit-Remaining"))
            reset = float(self._header_value(headers, "X-RateLimit-Reset"))
        except (TypeError, ValueError):
            return
        with self._rate_limit_lock:
            current = self._rate_limits.get(resource)
            if current is None or reset > current[1]:
                self._rate_limits[resource] = (remaining, reset)
            elif reset == current[1]:
                self._rate_limits[resource] = (min(current[0], remaining), reset)

    def _has_waitable_rate_limit(self, url: str) -> bool:
        resource = self._rate_resource(url)
        if not resource:
            return False
        with self._rate_limit_lock:
            state = self._rate_limits.get(resource)
        if state is None:
            return False
        remaining, reset = state
        delay = reset - time.time() + 1
        return remaining <= 1 and delay > 0

    def _wait_for_rate_slot(self, url: str) -> None:
        resource = self._rate_resource(url)
        if not resource:
            return
        while True:
            with self._rate_limit_lock:
                state = self._rate_limits.get(resource)
                if state is None:
                    return
                remaining, reset = state
                now = time.time()
                if reset <= now:
                    self._rate_limits.pop(resource, None)
                    return
                if remaining > 1:
                    self._rate_limits[resource] = (remaining - 1, reset)
                    return
                delay = reset - now + 1
            time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "issuepilot/0.1",
            "Content-Type": "application/json",
        }
        if self.token:
            headers["Authorization"] = "Bearer {}".format(self.token)
        if self._prefer_curl and shutil.which("curl"):
            return self._request_with_curl(method, url, body, headers)
        request = urllib.request.Request(
            url, data=body, method=method, headers=headers
        )
        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_slot(url)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    self._record_rate_limit(url, response.headers)
                    return json.loads(raw.decode("utf-8")) if raw else None
            except urllib.error.HTTPError as exc:
                self._record_rate_limit(url, exc.headers)
                details = exc.read().decode("utf-8", errors="replace")
                try:
                    message = json.loads(details).get("message", details)
                except json.JSONDecodeError:
                    message = details
                retryable = exc.code in (429, 500, 502, 503, 504)
                retry_after = _retry_after(exc.headers.get("Retry-After"), attempt)
                if retryable and attempt < self.max_retries:
                    time.sleep(retry_after)
                    continue
                remaining = exc.headers.get("X-RateLimit-Remaining")
                if exc.code == 403 and remaining == "0":
                    reset = exc.headers.get("X-RateLimit-Reset", "unknown")
                    message = "{} (rate limit resets at Unix time {})".format(message, reset)
                error_type = (
                    GitHubRateLimitError
                    if _is_rate_limited(exc.code, message, remaining)
                    else GitHubError
                )
                if (
                    error_type is GitHubRateLimitError
                    and attempt < self.max_retries
                    and self._has_waitable_rate_limit(url)
                ):
                    continue
                raise error_type(
                    "GitHub API {}: {}".format(exc.code, message)
                ) from exc
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                ssl.SSLError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                if shutil.which("curl"):
                    self._prefer_curl = True
                    return self._request_with_curl(method, url, body, headers)
                if attempt < self.max_retries:
                    time.sleep(_retry_after(None, attempt))
                    continue
                reason = getattr(exc, "reason", exc)
                raise GitHubError("Cannot reach GitHub: {}".format(reason)) from exc
        raise GitHubError("GitHub request failed after retries")

    def _request_with_curl(
        self,
        method: str,
        url: str,
        body: Optional[bytes],
        headers: Dict[str, str],
    ) -> Any:
        curl = shutil.which("curl")
        if not curl:
            raise GitHubError("Cannot reach GitHub and curl is unavailable")
        header_descriptor, header_name = tempfile.mkstemp(
            prefix="issuepilot-github-headers-"
        )
        header_path = Path(header_name)
        response_header_descriptor, response_header_name = tempfile.mkstemp(
            prefix="issuepilot-github-response-headers-"
        )
        os.close(response_header_descriptor)
        response_header_path = Path(response_header_name)
        body_path: Optional[Path] = None
        try:
            with os.fdopen(header_descriptor, "w", encoding="utf-8") as handle:
                for key, value in headers.items():
                    handle.write("{}: {}\n".format(key, value))
            header_path.chmod(0o600)
            command = [
                curl,
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(self.timeout),
                "--retry",
                str(self.max_retries),
                "--request",
                method,
                "--header",
                "@{}".format(header_path),
                "--dump-header",
                str(response_header_path),
                "--write-out",
                "\n%{http_code}",
            ]
            if body is not None:
                body_descriptor, body_name = tempfile.mkstemp(
                    prefix="issuepilot-github-body-"
                )
                body_path = Path(body_name)
                with os.fdopen(body_descriptor, "wb") as handle:
                    handle.write(body)
                body_path.chmod(0o600)
                command.extend(["--data-binary", "@{}".format(body_path)])
            command.append(url)
            for attempt in range(self.max_retries + 1):
                self._wait_for_rate_slot(url)
                result = subprocess.run(
                    command, text=True, capture_output=True, check=False
                )
                if result.returncode != 0:
                    raise GitHubError(
                        "Cannot reach GitHub: {}".format(
                            result.stderr.strip()
                            or "curl exit {}".format(result.returncode)
                        )
                    )
                try:
                    response_body, status_text = result.stdout.rsplit("\n", 1)
                    status = int(status_text)
                except (ValueError, TypeError) as exc:
                    raise GitHubError("GitHub returned an invalid HTTP response") from exc
                response_headers: Dict[str, str] = {}
                for line in response_header_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        response_headers[key.strip().lower()] = value.strip()
                self._record_rate_limit(url, response_headers)
                if status >= 400:
                    try:
                        message = json.loads(response_body).get(
                            "message", response_body
                        )
                    except json.JSONDecodeError:
                        message = response_body
                    remaining = self._header_value(
                        response_headers, "X-RateLimit-Remaining"
                    )
                    error_type = (
                        GitHubRateLimitError
                        if _is_rate_limited(status, message, remaining)
                        else GitHubError
                    )
                    if (
                        error_type is GitHubRateLimitError
                        and attempt < self.max_retries
                        and self._has_waitable_rate_limit(url)
                    ):
                        continue
                    raise error_type("GitHub API {}: {}".format(status, message))
                return json.loads(response_body) if response_body else None
            raise GitHubError("GitHub request failed after retries")
        finally:
            for path in (header_path, response_header_path, body_path):
                if path is not None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        return self._request("POST", path, payload=payload)

    def viewer(self) -> str:
        return str(self.get("/user")["login"])

    def viewer_identity(self) -> tuple:
        data = self.get("/user")
        login = str(data["login"])
        return login, "{}+{}@users.noreply.github.com".format(data["id"], login)

    def get_repo(self, full_name: str) -> Repository:
        return Repository.from_api(self.get("/repos/{}".format(full_name)))

    def get_issue(self, full_name: str, number: int) -> Issue:
        repo = self.get_repo(full_name)
        data = self.get("/repos/{}/issues/{}".format(full_name, number))
        if "pull_request" in data:
            raise GitHubError("The URL points to a pull request, not an issue")
        issue = Issue.from_api(repo, data)
        discussion = []
        page = 1
        while True:
            comments = self.get(
                "/repos/{}/issues/{}/comments".format(full_name, number),
                {"per_page": 100, "page": page},
            )
            for comment in comments:
                discussion.append(
                    {
                        "author": str(
                            (comment.get("user") or {}).get("login") or "unknown"
                        ),
                        "author_association": str(
                            comment.get("author_association") or ""
                        ),
                        "created_at": str(comment.get("created_at") or ""),
                        "updated_at": str(comment.get("updated_at") or ""),
                        "url": str(comment.get("html_url") or issue.html_url),
                        "body": str(comment.get("body") or ""),
                    }
                )
            if len(comments) < 100:
                break
            page += 1
        return replace(issue, discussion=discussion)

    def refresh_issue(self, issue: Issue) -> Issue:
        """Refresh mutable issue state without re-fetching repository metadata."""
        data = self.get(
            "/repos/{}/issues/{}".format(issue.repo.full_name, issue.number)
        )
        if "pull_request" in data:
            raise GitHubError("The candidate now points to a pull request")
        return Issue.from_api(issue.repo, data)

    def search_repositories(
        self,
        min_stars: int,
        languages: Iterable[str],
        limit: int,
        max_idle_days: int = 180,
        excluded_topics: Optional[Iterable[str]] = None,
        page: int = 1,
        max_stars: Optional[int] = None,
        sort_mode: str = "recommended",
        interests: Optional[dict] = None,
    ) -> List[Repository]:
        repositories: Dict[str, Repository] = {}
        from .interests import match_repository
        excluded = {value.lower() for value in (excluded_topics or [])}
        language_list = list(languages) or [""]
        desired_per_language = max(
            1, (limit + len(language_list) - 1) // len(language_list)
        )
        per_language = min(100, desired_per_language * 4)
        pushed_after = (datetime.now(timezone.utc) - timedelta(days=max_idle_days)).date()

        repository_sort = {
            "recommended": ("stars", "desc"),
            "stars_desc": ("stars", "desc"),
            "stars_asc": ("stars", "asc"),
            "newest_issues": ("updated", "desc"),
            "recently_updated": ("updated", "desc"),
        }
        if sort_mode not in repository_sort:
            raise ValueError("Unsupported discovery sort mode: {}".format(sort_mode))
        sort, order = repository_sort[sort_mode]
        star_query = (
            "stars:{}..{}".format(min_stars, max_stars)
            if max_stars is not None
            else "stars:>={}".format(min_stars)
        )

        def search_language(language: str) -> tuple:
            terms = [
                star_query,
                "pushed:>={}".format(pushed_after.isoformat()),
                "archived:false",
                "fork:false",
            ]
            if language:
                terms.append('language:"{}"'.format(language.replace('"', "")))
            data = self.get(
                "/search/repositories",
                {
                    "q": " ".join(terms),
                    "sort": sort,
                    "order": order,
                    "per_page": per_language,
                    "page": max(1, page),
                },
            )
            items = list(data.get("items", []))
            available = min(
                1000, max(0, int(data.get("total_count", len(items))))
            )
            exhausted = page * per_language >= available
            return items, exhausted

        exhausted_languages = []
        self.repository_search_star_values = []
        with ThreadPoolExecutor(
            max_workers=min(5, len(language_list))
        ) as executor:
            results = executor.map(search_language, language_list)
            for items, exhausted in results:
                exhausted_languages.append(exhausted)
                for item in items:
                    repo = Repository.from_api(item)
                    self.repository_search_star_values.append(repo.stars)
                    if (
                        not repo.archived
                        and not repo.fork
                        and not excluded.intersection(repo.topics)
                        and match_repository(repo, interests) is not None
                    ):
                        repositories[repo.full_name] = repo
        self.repository_search_exhausted = bool(exhausted_languages) and all(
            exhausted_languages
        )
        if sort_mode == "stars_asc":
            ordered = sorted(repositories.values(), key=lambda repo: repo.stars)
        elif sort_mode in ("newest_issues", "recently_updated"):
            ordered = sorted(
                repositories.values(), key=lambda repo: repo.pushed_at, reverse=True
            )
        else:
            ordered = sorted(
                repositories.values(), key=lambda repo: repo.stars, reverse=True
            )
        return ordered[:limit]

    def search_issues(
        self,
        repo: Repository,
        limit: int,
        labels: Optional[Iterable[str]] = None,
        sort_mode: str = "recommended",
    ) -> List[Issue]:
        found: Dict[int, Issue] = {}
        issue_sort = "created" if sort_mode == "newest_issues" else "updated"
        for label in labels or ("good first issue", "help wanted"):
            data = self.get(
                "/repos/{}/issues".format(repo.full_name),
                {
                    "state": "open",
                    "labels": label,
                    "sort": issue_sort,
                    "direction": "desc",
                    "per_page": min(limit, 100),
                },
            )
            for item in data:
                if "pull_request" in item:
                    continue
                issue = Issue.from_api(repo, item)
                if not issue.locked and not issue.assignee:
                    found[issue.number] = issue
        return list(found.values())

    def inspect_live_issue(
        self, issue: Issue, recent_days: int = 45
    ) -> tuple:
        """Refresh an issue and inspect activity in one authenticated request."""
        owner, name = issue.repo.full_name.split("/", 1)
        query = """
        query IssuePilotInspection(
          $owner: String!, $name: String!, $number: Int!, $prQuery: String!
        ) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              number title body url state locked createdAt updatedAt
              labels(first: 50) { nodes { name } }
              assignees(first: 1) { nodes { login } }
              comments(last: 100) {
                totalCount
                nodes {
                  author { __typename login }
                  createdAt
                  url
                  body
                }
              }
              timelineItems(
                last: 100,
                itemTypes: [CROSS_REFERENCED_EVENT, REFERENCED_EVENT]
              ) {
                nodes {
                  __typename
                  ... on CrossReferencedEvent {
                    createdAt
                    source {
                      __typename
                      ... on PullRequest {
                        number title body url state
                      }
                    }
                  }
                  ... on ReferencedEvent {
                    createdAt
                    commit { oid url committedDate }
                  }
                }
              }
            }
          }
          search(query: $prQuery, type: ISSUE, first: 20) {
            nodes {
              ... on PullRequest { number title body url state }
            }
          }
        }
        """
        response = self.post(
            "/graphql",
            {
                "query": query,
                "variables": {
                    "owner": owner,
                    "name": name,
                    "number": issue.number,
                    "prQuery": "repo:{} is:pr is:open {}".format(
                        issue.repo.full_name, issue.number
                    ),
                },
            },
        )
        if response.get("errors"):
            message = "; ".join(
                str(item.get("message") or item)
                for item in response.get("errors", [])
            )
            error_type = (
                GitHubRateLimitError
                if "rate limit" in message.lower()
                else GitHubError
            )
            raise error_type("GitHub GraphQL: {}".format(message))
        data = response.get("data") or {}
        node = ((data.get("repository") or {}).get("issue") or {})
        if not node:
            raise GitHubError("GitHub issue is no longer available")
        label_nodes = (node.get("labels") or {}).get("nodes") or []
        assignee_nodes = (node.get("assignees") or {}).get("nodes") or []
        comments = node.get("comments") or {}
        refreshed = Issue.from_api(
            issue.repo,
            {
                "number": node.get("number", issue.number),
                "title": node.get("title") or "",
                "body": node.get("body") or "",
                "html_url": node.get("url") or issue.html_url,
                "state": str(node.get("state") or "open").lower(),
                "locked": bool(node.get("locked")),
                "created_at": node.get("createdAt") or "",
                "updated_at": node.get("updatedAt") or "",
                "labels": [
                    {"name": item.get("name") or ""} for item in label_nodes
                ],
                "assignee": assignee_nodes[0] if assignee_nodes else None,
                "comments": int(comments.get("totalCount") or 0),
            },
        )
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        open_pr_url: Optional[str] = None
        recent_commit_url: Optional[str] = None
        active_claim_url: Optional[str] = None
        likely_resolved_url: Optional[str] = None
        warnings: List[str] = []
        for comment in comments.get("nodes") or []:
            author = comment.get("author") or {}
            if str(author.get("__typename") or "").lower() == "bot":
                continue
            body = str(comment.get("body") or "")
            url = str(comment.get("url") or issue.html_url)
            if _is_recent(str(comment.get("createdAt") or ""), recent_cutoff) and _matches_any(
                body, CLAIM_PATTERNS
            ):
                active_claim_url = url
            if _matches_any(body, RESOLVED_PATTERNS):
                likely_resolved_url = url
        timeline = (node.get("timelineItems") or {}).get("nodes") or []
        for event in timeline:
            event_type = str(event.get("__typename") or "")
            if event_type == "CrossReferencedEvent":
                source = event.get("source") or {}
                if source.get("__typename") == "PullRequest":
                    url = source.get("url")
                    if source.get("state") == "OPEN" and url:
                        open_pr_url = str(url)
                    elif url:
                        warnings.append("发现已关闭的关联 PR：{}".format(url))
            if event_type == "ReferencedEvent":
                commit = event.get("commit") or {}
                created_at = str(
                    commit.get("committedDate") or event.get("createdAt") or ""
                )
                if commit.get("oid") and _is_recent(created_at, recent_cutoff):
                    recent_commit_url = str(
                        commit.get("url")
                        or "{}/commit/{}".format(
                            issue.repo.html_url, commit.get("oid")
                        )
                    )
        if not open_pr_url:
            for pull_request in (data.get("search") or {}).get("nodes") or []:
                if not pull_request:
                    continue
                item = {
                    "title": pull_request.get("title") or "",
                    "body": pull_request.get("body") or "",
                }
                if (
                    pull_request.get("state") == "OPEN"
                    and _pull_request_mentions_issue(item, refreshed)
                    and pull_request.get("url")
                ):
                    open_pr_url = str(pull_request.get("url"))
                    break
        checks = ["已用单次 GitHub 请求核验 Issue、讨论、时间线和开放 PR"]
        if not open_pr_url:
            checks.append("未发现关联的开放 PR")
        if not recent_commit_url:
            checks.append("最近 {} 天未发现关联 Commit".format(recent_days))
        if not active_claim_url:
            checks.append("最近 {} 天未发现明确认领留言".format(recent_days))
        if not likely_resolved_url:
            checks.append("未发现已实现、已修复或重复问题的明确留言")
        return refreshed, IssueActivity(
            open_pr_url=open_pr_url,
            recent_commit_url=recent_commit_url,
            active_claim_url=active_claim_url,
            likely_resolved_url=likely_resolved_url,
            checks=checks,
            warnings=warnings,
        )

    def inspect_issue_activity(
        self, issue: Issue, recent_days: int = 45
    ) -> IssueActivity:
        """Inspect PR, commit, and explicit contributor activity signals."""
        events = self.get(
            "/repos/{}/issues/{}/timeline".format(
                issue.repo.full_name, issue.number
            ),
            {"per_page": 100},
        )
        open_pr_url: Optional[str] = None
        recent_commit_url: Optional[str] = None
        active_claim_url: Optional[str] = None
        likely_resolved_url: Optional[str] = None
        warnings: List[str] = []
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
        for event in events:
            event_type = str(event.get("event") or "")
            if event_type == "cross-referenced":
                source_issue = ((event.get("source") or {}).get("issue") or {})
                if "pull_request" in source_issue:
                    url = source_issue.get("html_url")
                    if source_issue.get("state") == "open" and url:
                        open_pr_url = str(url)
                    elif url:
                        warnings.append("发现已关闭的关联 PR：{}".format(url))
            created_at = str(event.get("created_at") or "")
            recent = _is_recent(created_at, recent_cutoff)
            commit_id = event.get("commit_id")
            if recent and commit_id:
                recent_commit_url = str(
                    event.get("commit_url")
                    or "{}/commit/{}".format(issue.repo.html_url, commit_id)
                )
            if recent and event_type == "commented":
                body = str(event.get("body") or "")
                actor = event.get("actor") or event.get("user") or {}
                actor_type = str(actor.get("type") or "")
                if actor_type.lower() != "bot" and _matches_any(
                    body, CLAIM_PATTERNS
                ):
                    active_claim_url = str(event.get("html_url") or issue.html_url)
            if event_type == "commented":
                body = str(event.get("body") or "")
                actor = event.get("actor") or event.get("user") or {}
                actor_type = str(actor.get("type") or "")
                if actor_type.lower() != "bot" and _matches_any(
                    body, RESOLVED_PATTERNS
                ):
                    likely_resolved_url = str(
                        event.get("html_url") or issue.html_url
                    )
        # GitHub does not always expose a PR mention in an issue's timeline or
        # Development section. Search the repository and validate the match
        # locally so an unrelated PR containing the same digits is not enough.
        if not open_pr_url:
            data = self.get(
                "/search/issues",
                {
                    "q": "repo:{} is:pr {}".format(
                        issue.repo.full_name, issue.number
                    ),
                    "per_page": 20,
                },
            )
            for item in data.get("items", []):
                if _pull_request_mentions_issue(item, issue):
                    url = item.get("html_url")
                    if url and item.get("state") == "open":
                        open_pr_url = str(url)
                        break
                    if url:
                        warning = "发现已关闭的相关 PR：{}".format(url)
                        if warning not in warnings:
                            warnings.append(warning)
        checks = ["已检查 Issue 时间线和仓库内开放 PR"]
        if not open_pr_url:
            checks.append("未发现关联的开放 PR")
        if not recent_commit_url:
            checks.append("最近 {} 天未发现关联 Commit".format(recent_days))
        if not active_claim_url:
            checks.append("最近 {} 天未发现明确认领留言".format(recent_days))
        if not likely_resolved_url:
            checks.append("未发现已实现、已修复或重复问题的明确留言")
        return IssueActivity(
            open_pr_url=open_pr_url,
            recent_commit_url=recent_commit_url,
            active_claim_url=active_claim_url,
            likely_resolved_url=likely_resolved_url,
            checks=checks,
            warnings=warnings,
        )

    def find_open_linked_pull_request(self, issue: Issue) -> Optional[str]:
        """Return an open PR that cross-references the issue, if one exists."""
        return self.inspect_issue_activity(issue).open_pr_url

    def fork(self, repo: Repository, owner: str, wait_seconds: int = 60) -> str:
        if repo.full_name.split("/", 1)[0].lower() == owner.lower():
            return repo.full_name
        fork_name = "{}/{}".format(owner, repo.full_name.split("/", 1)[1])
        try:
            existing = self.get("/repos/{}".format(fork_name))
            if existing.get("fork"):
                parent = (existing.get("parent") or {}).get("full_name", "")
                if parent.lower() == repo.full_name.lower():
                    return fork_name
                raise GitHubError(
                    "Repository {} already exists but is not a fork of {}".format(
                        fork_name, repo.full_name
                    )
                )
        except GitHubError as exc:
            if "404" not in str(exc):
                raise
        self.post("/repos/{}/forks".format(repo.full_name), {})
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                self.get("/repos/{}".format(fork_name))
                return fork_name
            except GitHubError as exc:
                if "404" not in str(exc):
                    raise
                time.sleep(2)
        raise GitHubError("Fork was not ready after {} seconds".format(wait_seconds))

    def find_open_pull_request(self, repo: Repository, head: str) -> Optional[str]:
        data = self.get(
            "/repos/{}/pulls".format(repo.full_name),
            {"state": "open", "head": head, "per_page": 1},
        )
        if data:
            return str(data[0]["html_url"])
        return None

    def create_pull_request(
        self,
        repo: Repository,
        head: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        data = self.post(
            "/repos/{}/pulls".format(repo.full_name),
            {
                "title": title,
                "head": head,
                "base": repo.default_branch,
                "body": body,
                "draft": draft,
            },
        )
        return str(data["html_url"])


def run_git(args: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitHubError("git {} failed: {}".format(args[0], detail))
    return result


def _retry_after(value: Optional[str], attempt: int) -> float:
    if value:
        try:
            return max(0.0, min(float(value), 60.0))
        except ValueError:
            pass
    return min(2.0 ** attempt, 8.0)


def push_with_token(cwd: Path, remote: str, branch: str, token: str) -> None:
    if not token:
        raise GitHubError("A GitHub token is required to push")
    script = "#!/bin/sh\ncase \"$1\" in\n  *Username*) printf '%s\\n' 'x-access-token' ;;\n  *) printf '%s\\n' \"$ISSUEPILOT_PUSH_TOKEN\" ;;\nesac\n"
    descriptor, name = tempfile.mkstemp(prefix="issuepilot-askpass-")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(script)
        path.chmod(0o700)
        env = os.environ.copy()
        env.update(
            {
                "GIT_ASKPASS": str(path),
                "GIT_TERMINAL_PROMPT": "0",
                "ISSUEPILOT_PUSH_TOKEN": token,
            }
        )
        result = subprocess.run(
            ["git", "push", "--no-verify", "--set-upstream", remote, branch],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise GitHubError("git push failed: {}".format(result.stderr.strip()))
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
