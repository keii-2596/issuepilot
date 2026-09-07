"""Local-only HTTP dashboard for IssuePilot."""

import json
import mimetypes
import re
import selectors
import subprocess
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .config import DISCOVERY_SORT_MODES, Config, github_cli_path
from .discovery import discover
from .github import GitHubClient, GitHubError
from .interests import DOMAIN_PRESETS, normalize_interests
from .state import (
    advance_discovery_page,
    archive_run,
    candidate_archive_summary,
    discovery_page,
    list_manifests,
    save_discovery_run,
)
from .workflow import parse_issue_url, publish_prepared, run_issue


MAX_BODY_BYTES = 32 * 1024
GITHUB_DEVICE_URL = "https://github.com/login/device"
GITHUB_DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")
DISCOVERY_SCAN_ID = re.compile(r"^[A-Za-z0-9-]{8,80}$")
_DISCOVERY_CANCEL_EVENTS: Dict[str, threading.Event] = {}
_DISCOVERY_CANCEL_LOCK = threading.Lock()


def _github_auth_command(executable: str) -> list[str]:
    return [
        executable,
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--git-protocol",
        "https",
        "--web",
        "--clipboard",
    ]


def _extract_device_code(output: str) -> Optional[str]:
    match = GITHUB_DEVICE_CODE.search(output.upper())
    return match.group(0) if match else None


def _drain_auth_process(process: subprocess.Popen[str]) -> None:
    if process.stdout:
        for _line in process.stdout:
            pass
    process.wait()


def _start_github_auth(executable: str, timeout_seconds: float = 12.0) -> str:
    process = subprocess.Popen(
        _github_auth_command(executable),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    if process.stdout is None:
        process.terminate()
        raise RuntimeError("无法读取 GitHub 设备授权码")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = ""
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            if not selector.select(min(0.5, remaining)):
                if process.poll() is not None:
                    break
                continue
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue
            output += line
            code = _extract_device_code(output)
            if code:
                threading.Thread(
                    target=_drain_auth_process, args=(process,), daemon=True
                ).start()
                return code
    finally:
        selector.close()
    if process.poll() is None:
        process.terminate()
    detail = output.strip().splitlines()[-1] if output.strip() else "未生成设备码"
    raise RuntimeError("GitHub 登录未能启动：{}".format(detail))


def _request_integer(
    data: Dict[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = data.get(name, default)
    if isinstance(raw, bool):
        raise ValueError("{} must be an integer".format(name))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must be an integer".format(name)) from exc
    if value < minimum or value > maximum:
        raise ValueError(
            "{} must be between {} and {}".format(name, minimum, maximum)
        )
    return value


def _request_languages(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_languages = value.split(",")
    elif isinstance(value, list):
        raw_languages = value
    else:
        raise ValueError("languages must be a list or comma-separated string")
    languages = []
    for raw in raw_languages:
        if not isinstance(raw, str):
            raise ValueError("each language must be text")
        language = raw.strip()
        if not language:
            continue
        if len(language) > 40:
            raise ValueError("language names must be at most 40 characters")
        if language not in languages:
            languages.append(language)
    if not languages:
        raise ValueError("select at least one language")
    if len(languages) > 12:
        raise ValueError("select no more than 12 languages")
    return languages


def _request_sort_mode(value: Any, default: str) -> str:
    mode = str(value or default).strip()
    if mode not in DISCOVERY_SORT_MODES:
        raise ValueError(
            "sort_mode must be one of {}".format(", ".join(DISCOVERY_SORT_MODES))
        )
    return mode


def _request_scan_id(value: Any, create: bool = False) -> str:
    if value is None and create:
        return uuid.uuid4().hex
    scan_id = str(value or "").strip()
    if not DISCOVERY_SCAN_ID.fullmatch(scan_id):
        raise ValueError("invalid discovery scan id")
    return scan_id


def _register_discovery_scan(scan_id: str) -> threading.Event:
    with _DISCOVERY_CANCEL_LOCK:
        if scan_id in _DISCOVERY_CANCEL_EVENTS:
            raise RuntimeError("this discovery scan is already running")
        event = threading.Event()
        _DISCOVERY_CANCEL_EVENTS[scan_id] = event
        return event


def _cancel_discovery_scan(scan_id: str) -> bool:
    with _DISCOVERY_CANCEL_LOCK:
        event = _DISCOVERY_CANCEL_EVENTS.get(scan_id)
        if event is None:
            return False
        event.set()
        return True


def _release_discovery_scan(scan_id: str) -> None:
    with _DISCOVERY_CANCEL_LOCK:
        _DISCOVERY_CANCEL_EVENTS.pop(scan_id, None)


def _codex_logged_in(executable: Optional[str]) -> bool:
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "login", "status"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _candidate_dict(issue: Any) -> Dict[str, Any]:
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
        "comments": issue.comments,
        "summary": (issue.body or "").strip()[:320],
        "fit": issue.fit,
        "fit_reasons": issue.fit_reasons,
        "risk_reasons": issue.risk_reasons,
        "longevity_reason": issue.longevity_reason,
        "age_days": issue.age_days,
        "updated_days": issue.updated_days,
        "verification": issue.verification,
        "warnings": issue.warnings,
    }


def _outcome_dict(outcome: Any) -> Dict[str, Any]:
    return {
        "status": outcome.status,
        "issue": outcome.issue.html_url,
        "message": outcome.message,
        "workspace": str(outcome.workspace) if outcome.workspace else None,
        "pull_request": outcome.pr_url,
    }


def _scan_summary(metrics: Dict[str, object], next_page: int) -> Dict[str, Any]:
    start = int(metrics.get("start_page", 1))
    count = max(1, int(metrics.get("pages_scanned", 1)))
    page_limit = max(1, int(metrics.get("search_page_limit", 250)))
    pages = [((start - 1 + offset) % page_limit) + 1 for offset in range(count)]
    return {
        "pages": pages,
        "repositories_scanned": int(metrics.get("repositories_scanned", 0)),
        "issues_collected": int(metrics.get("issues_collected", 0)),
        "issues_filtered_by_age": int(metrics.get("issues_filtered_by_age", 0)),
        "issues_verified": int(metrics.get("issues_verified", 0)),
        "target_count": int(metrics.get("target_count", 0)),
        "target_reached": bool(metrics.get("target_reached", False)),
        "search_exhausted": bool(metrics.get("search_exhausted", False)),
        "scan_limit_reached": bool(metrics.get("scan_limit_reached", False)),
        "cancelled": bool(metrics.get("cancelled", False)),
        "search_page_limit": page_limit,
        "star_windows_scanned": int(metrics.get("star_windows_scanned", 1)),
        "next_page": next_page,
    }


def dashboard_status(config: Config) -> Dict[str, Any]:
    codex_authenticated = _codex_logged_in(config.codex_path)
    github_authenticated = bool(config.github_token)
    github_user = None
    github_error = None
    if github_authenticated:
        try:
            github_user = GitHubClient(config.github_token).viewer()
        except GitHubError as exc:
            github_error = str(exc)
    prepared = list_manifests(config)
    return {
        "github_authenticated": github_authenticated and not github_error,
        "github_mode": "authenticated" if github_authenticated and not github_error else "anonymous-read-only",
        "github_user": github_user,
        "github_error": github_error,
        "github_cli_available": bool(github_cli_path()),
        "codex_found": bool(config.codex_path),
        "codex_authenticated": codex_authenticated,
        "publish_unlocked": config.allow_publish,
        "read_only_ready": codex_authenticated,
        "publish_ready": github_authenticated and not github_error and codex_authenticated and config.allow_publish,
        "workspace": str(config.workspace_root),
        "filters": {
            "interests": normalize_interests(config.interests or {}),
            "min_stars": config.min_stars,
            "max_stars": config.max_stars,
            "languages": config.languages,
            "repo_limit": config.repo_limit,
            "issues_per_repo": config.issues_per_repo,
            "max_repo_idle_days": config.max_repo_idle_days,
            "max_issue_idle_days": config.max_issue_idle_days,
            "max_issue_age_days": config.max_issue_age_days,
            "issue_labels": config.issue_labels,
            "active_work_days": config.active_work_days,
            "discovery_scan_pages": config.discovery_scan_pages,
            "discovery_sort": config.discovery_sort,
        },
        "discovery_next_page": discovery_page(config),
        "domain_presets": [{"id": key, "label": value[0]} for key, value in DOMAIN_PRESETS.items()],
        "candidate_archive": candidate_archive_summary(config),
        "prepared": prepared,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "IssuePilot/0.1"

    @property
    def static_root(self) -> Path:
        return Path(getattr(self.server, "static_root"))

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the terminal useful without logging request bodies or secrets.
        super().log_message(format, *args)

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _json(self, value: Any, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid request length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large")
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Expected a JSON request body") from exc
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.scheme in ("http", "https") and parsed.hostname in (
            "127.0.0.1",
            "localhost",
            "::1",
        )

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            try:
                self._json(dashboard_status(Config.load()))
            except (RuntimeError, ValueError, OSError) as exc:
                self._json({"error": str(exc)}, 500)
            return
        if path == "/api/health":
            self._json({"ok": True})
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        if not self._origin_is_local():
            self._json({"error": "Only local dashboard requests are accepted"}, 403)
            return
        path = urlparse(self.path).path
        try:
            data = self._read_json()
            if path == "/api/discover/cancel":
                scan_id = _request_scan_id(data.get("scan_id"))
                self._json(
                    {
                        "status": "stopping",
                        "scan_id": scan_id,
                        "found": _cancel_discovery_scan(scan_id),
                    }
                )
                return
            config = Config.load()
            if path == "/api/interests":
                interests = normalize_interests(data)
                preferences = config.workspace_root.parent / "interests.json"
                preferences.parent.mkdir(parents=True, exist_ok=True)
                temporary = preferences.with_name("interests-{}.tmp".format(uuid.uuid4().hex))
                temporary.write_text(json.dumps(interests, ensure_ascii=False), encoding="utf-8")
                temporary.replace(preferences)
                self._json({"interests": interests})
                return
            client = GitHubClient(config.github_token)
            if path == "/api/discover":
                scan_id = _request_scan_id(data.get("scan_id"), create=True)
                interests = normalize_interests(data.get("interests", config.interests) or {})
                limit = _request_integer(data, "limit", 12, 1, 50)
                min_stars = _request_integer(data, "min_stars", config.min_stars, 1, 1_000_000)
                max_stars = _request_integer(data, "max_stars", config.max_stars, 1, 1_000_000)
                if max_stars < min_stars:
                    raise ValueError("max_stars must be at least min_stars")
                repo_limit = _request_integer(data, "repo_limit", config.repo_limit, 1, 25)
                issues_per_repo = _request_integer(
                    data, "issues_per_repo", config.issues_per_repo, 1, 100
                )
                max_repo_idle_days = _request_integer(
                    data, "max_repo_idle_days", config.max_repo_idle_days, 1, 3650
                )
                max_issue_idle_days = _request_integer(
                    data, "max_issue_idle_days", config.max_issue_idle_days, 1, 3650
                )
                max_issue_age_days = _request_integer(
                    data, "max_issue_age_days", config.max_issue_age_days, 1, 3650
                )
                active_work_days = _request_integer(
                    data, "active_work_days", config.active_work_days, 1, 365
                )
                languages = _request_languages(data.get("languages", config.languages))
                issue_labels = _request_languages(
                    data.get("issue_labels", config.issue_labels)
                )
                sort_mode = _request_sort_mode(
                    data.get("sort_mode"), config.discovery_sort
                )
                start_page = discovery_page(config)
                metrics: Dict[str, object] = {}
                cancel_event = _register_discovery_scan(scan_id)
                try:
                    issues = discover(
                        client,
                        min_stars,
                        languages,
                        repo_limit,
                        issues_per_repo,
                        limit,
                        max_repo_idle_days,
                        config.excluded_topics,
                        max_issue_idle_days,
                        issue_labels,
                        active_work_days,
                        start_page=start_page,
                        max_scan_pages=config.discovery_scan_pages,
                        concurrency=config.discovery_concurrency,
                        metrics=metrics,
                        max_stars=max_stars,
                        max_issue_age_days=max_issue_age_days,
                        sort_mode=sort_mode,
                        should_stop=cancel_event.is_set,
                        interests=interests,
                    )
                finally:
                    _release_discovery_scan(scan_id)
                next_page = advance_discovery_page(
                    config,
                    start_page,
                    int(metrics.get("pages_scanned", 1)),
                    int(metrics.get("search_page_limit", 250)),
                )
                filters = {
                    "interests": interests,
                    "min_stars": min_stars,
                    "max_stars": max_stars,
                    "languages": languages,
                    "repo_limit": repo_limit,
                    "issues_per_repo": issues_per_repo,
                    "max_repo_idle_days": max_repo_idle_days,
                    "max_issue_idle_days": max_issue_idle_days,
                    "max_issue_age_days": max_issue_age_days,
                    "issue_labels": issue_labels,
                    "active_work_days": active_work_days,
                    "sort_mode": sort_mode,
                    "limit": limit,
                }
                scan = _scan_summary(metrics, next_page)
                archive = save_discovery_run(config, issues, filters, scan)
                self._json({
                    "scan_id": scan_id,
                    "candidates": [_candidate_dict(issue) for issue in issues],
                    "scan": scan,
                    "filters": filters,
                    "candidate_archive": archive,
                })
                return
            if path == "/api/github/connect":
                if config.github_token:
                    self._json({"status": "connected"})
                    return
                gh = github_cli_path()
                if not gh:
                    raise RuntimeError("未找到 GitHub CLI，请先安装 gh 后再连接")
                device_code = _start_github_auth(gh)
                self._json(
                    {
                        "status": "started",
                        "verification_url": GITHUB_DEVICE_URL,
                        "device_code": device_code,
                        "code_copied": True,
                    },
                    202,
                )
                return
            if path == "/api/run":
                issue_url = str(data.get("issue", ""))
                full_name, number = parse_issue_url(issue_url)
                issue = client.get_issue(full_name, number)
                self._json(_outcome_dict(run_issue(client, config, issue, False)))
                return
            if path == "/api/publish":
                if data.get("confirmation") != "PUBLISH":
                    raise ValueError("Publishing requires explicit confirmation")
                if not config.github_token:
                    raise RuntimeError("Connect a GitHub account before publishing")
                issue_url = str(data.get("issue", ""))
                self._json(_outcome_dict(publish_prepared(client, config, issue_url)))
                return
            if path == "/api/archive":
                if data.get("confirmation") != "ARCHIVE":
                    raise ValueError("Archiving requires explicit confirmation")
                issue_url = str(data.get("issue", ""))
                full_name, number = parse_issue_url(issue_url)
                destination = archive_run(config, full_name, number)
                self._json({"status": "archived", "issue": issue_url, "archive": str(destination)})
                return
            self._json({"error": "Unknown API endpoint"}, 404)
        except (GitHubError, RuntimeError, ValueError, OSError) as exc:
            self._json({"error": str(exc)}, 400)

    def _serve_static(self, request_path: str) -> None:
        root = self.static_root.resolve()
        relative = request_path.lstrip("/") or "index.html"
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json({"error": "Invalid path"}, 400)
            return
        if not target.is_file() and "." not in Path(relative).name:
            target = root / "index.html"
        if not target.is_file():
            self._json({"error": "Dashboard assets are missing. Rebuild the web frontend."}, 404)
            return
        payload = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(payload))
        self.wfile.write(payload)


def create_server(host: str, port: int, static_root: Optional[Path] = None) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("The dashboard may only listen on the local computer")
    root = static_root or Path(__file__).with_name("web_static")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.static_root = root  # type: ignore[attr-defined]
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = create_server(host, port)
    url = "http://{}:{}/".format(host, server.server_address[1])
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    print("IssuePilot dashboard: {}".format(url))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
