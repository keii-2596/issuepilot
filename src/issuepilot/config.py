import os
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load a small, non-executable subset of dotenv syntax."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ("GITHUB_TOKEN", "GH_TOKEN") and not key.startswith("ISSUEPILOT_"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _integer(
    name: str, default: int, minimum: int = 1, maximum: Optional[int] = None
) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("{} must be an integer".format(name)) from exc
    if value < minimum:
        raise ValueError("{} must be at least {}".format(name, minimum))
    if maximum is not None and value > maximum:
        raise ValueError("{} must be at most {}".format(name, maximum))
    return value


def _languages() -> List[str]:
    raw = os.getenv(
        "ISSUEPILOT_LANGUAGES", "Python,TypeScript,JavaScript,Go,Rust"
    )
    return [part.strip() for part in raw.split(",") if part.strip()]


def _excluded_topics() -> List[str]:
    raw = os.getenv(
        "ISSUEPILOT_EXCLUDED_TOPICS",
        "awesome-list,books,free-programming-books,interview,interview-resources,resources,lists",
    )
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _issue_labels() -> List[str]:
    raw = os.getenv("ISSUEPILOT_ISSUE_LABELS", "good first issue,help wanted")
    return [part.strip() for part in raw.split(",") if part.strip()]


DISCOVERY_SORT_MODES = (
    "recommended",
    "stars_desc",
    "stars_asc",
    "newest_issues",
    "recently_updated",
)


def _discovery_sort_mode() -> str:
    value = os.getenv("ISSUEPILOT_DISCOVERY_SORT", "recommended").strip()
    if value not in DISCOVERY_SORT_MODES:
        raise ValueError(
            "ISSUEPILOT_DISCOVERY_SORT must be one of {}".format(
                ", ".join(DISCOVERY_SORT_MODES)
            )
        )
    return value


def github_cli_path() -> Optional[str]:
    discovered = shutil.which("gh")
    if discovered:
        return discovered
    user_install = Path.home() / ".local" / "bin" / "gh"
    if user_install.is_file() and os.access(user_install, os.X_OK):
        return str(user_install)
    return None


def _github_token() -> Optional[str]:
    direct = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if direct:
        return direct.strip()
    gh = github_cli_path()
    if not gh:
        return None
    import subprocess

    result = subprocess.run(
        [gh, "auth", "token"], text=True, capture_output=True, check=False
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


@dataclass(frozen=True)
class Config:
    github_token: Optional[str]
    codex_path: Optional[str]
    codex_model: Optional[str]
    workspace_root: Path
    min_stars: int
    languages: List[str]
    repo_limit: int
    issues_per_repo: int
    max_changed_files: int
    max_diff_bytes: int
    allow_publish: bool
    codex_timeout_seconds: int = 1800
    max_repo_idle_days: int = 180
    excluded_topics: Optional[List[str]] = None
    max_issue_idle_days: int = 365
    issue_labels: Optional[List[str]] = None
    active_work_days: int = 45
    discovery_concurrency: int = 6
    discovery_scan_pages: int = 250
    max_stars: int = 1_000_000
    max_issue_age_days: int = 180
    discovery_sort: str = "recommended"
    interests: Optional[dict] = None

    @classmethod
    def load(cls) -> "Config":
        _load_dotenv()
        model = os.getenv("ISSUEPILOT_CODEX_MODEL", "").strip() or None
        root = Path(
            os.getenv("ISSUEPILOT_WORKSPACE", ".oss-agent/workspaces")
        ).expanduser().resolve()
        from .interests import normalize_interests
        preferences = root.parent / "interests.json"
        interests = normalize_interests({})
        if preferences.is_file():
            interests = normalize_interests(json.loads(preferences.read_text(encoding="utf-8")))
        config = cls(
            github_token=_github_token(),
            codex_path=shutil.which("codex"),
            codex_model=model,
            workspace_root=root,
            min_stars=_integer("ISSUEPILOT_MIN_STARS", 5000),
            languages=_languages(),
            repo_limit=_integer("ISSUEPILOT_REPO_LIMIT", 20, maximum=25),
            issues_per_repo=_integer(
                "ISSUEPILOT_ISSUES_PER_REPO", 5, maximum=100
            ),
            max_changed_files=_integer("ISSUEPILOT_MAX_CHANGED_FILES", 20),
            max_diff_bytes=_integer("ISSUEPILOT_MAX_DIFF_BYTES", 200000),
            allow_publish=os.getenv("ISSUEPILOT_ALLOW_PUBLISH") == "I_UNDERSTAND",
            codex_timeout_seconds=_integer(
                "ISSUEPILOT_CODEX_TIMEOUT_SECONDS", 1800, minimum=60, maximum=7200
            ),
            max_repo_idle_days=_integer(
                "ISSUEPILOT_MAX_REPO_IDLE_DAYS", 180, minimum=1, maximum=3650
            ),
            excluded_topics=_excluded_topics(),
            max_issue_idle_days=_integer(
                "ISSUEPILOT_MAX_ISSUE_IDLE_DAYS", 365, minimum=1, maximum=3650
            ),
            issue_labels=_issue_labels(),
            active_work_days=_integer(
                "ISSUEPILOT_ACTIVE_WORK_DAYS", 45, minimum=1, maximum=365
            ),
            discovery_concurrency=_integer(
                "ISSUEPILOT_DISCOVERY_CONCURRENCY", 6, minimum=1, maximum=8
            ),
            discovery_scan_pages=_integer(
                "ISSUEPILOT_DISCOVERY_SCAN_PAGES", 250, minimum=1, maximum=250
            ),
            max_stars=_integer(
                "ISSUEPILOT_MAX_STARS", 1_000_000, minimum=1, maximum=1_000_000
            ),
            max_issue_age_days=_integer(
                "ISSUEPILOT_MAX_ISSUE_AGE_DAYS", 180, minimum=1, maximum=3650
            ),
            discovery_sort=_discovery_sort_mode(),
            interests=interests,
        )
        if config.max_stars < config.min_stars:
            raise ValueError("ISSUEPILOT_MAX_STARS must be at least ISSUEPILOT_MIN_STARS")
        return config
