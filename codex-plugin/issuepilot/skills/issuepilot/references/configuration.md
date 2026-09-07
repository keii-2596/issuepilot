# IssuePilot configuration

IssuePilot reads `.env` from the working directory where its runner is started. It accepts only `GITHUB_TOKEN`, `GH_TOKEN`, and names beginning with `ISSUEPILOT_`.

## GitHub identity

- `GITHUB_TOKEN` or `GH_TOKEN`: optional for public discovery; required for publishing unless the GitHub CLI is installed and already authenticated.
- Ask the user to create a dedicated, minimum-permission, revocable credential suitable for forking, writing the fork, and creating a pull request. Never request the token value in chat or print it.
- Without authentication, keep using anonymous read-only discovery and warn that the GitHub API limit is lower.

## Publish lock

- `ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND` unlocks the deterministic publish command.
- The variable enables persistent CLI publishing but is not sufficient by itself: the user must still explicitly approve publishing the exact prepared diff in the current conversation.
- Do not set this value for the user and do not bypass it with manual Git or GitHub operations.
- For a single publication, an explicit request in the current conversation can instead authorize `issuepilot publish --issue URL --confirm-current-diff`. This confirmation applies only to that invocation, only to an existing prepared fingerprint, and still requires GitHub authentication and every normal pre-publish safety check. Never use it for `run --publish`, `autopilot --publish`, or an ambiguous request.

## Discovery and workspace

- `ISSUEPILOT_MIN_STARS` defaults to `5000`.
- `ISSUEPILOT_MAX_STARS` defaults to `1000000`; it must not be lower than the minimum.
- `ISSUEPILOT_LANGUAGES` defaults to `Python,TypeScript,JavaScript,Go,Rust`.
- `ISSUEPILOT_REPO_LIMIT` defaults to `20`.
- `ISSUEPILOT_ISSUES_PER_REPO` defaults to `5`.
- `ISSUEPILOT_MAX_REPO_IDLE_DAYS` defaults to `180`.
- `ISSUEPILOT_MAX_ISSUE_IDLE_DAYS` defaults to `365`.
- `ISSUEPILOT_MAX_ISSUE_AGE_DAYS` defaults to `180` and excludes issues created longer ago, independently of recent comments or updates.
- `ISSUEPILOT_ISSUE_LABELS` defaults to `good first issue,help wanted`; matching any listed label is sufficient.
- `ISSUEPILOT_ACTIVE_WORK_DAYS` defaults to `45` and controls how far back linked commits and natural-language contributor claims are treated as active work. Explicit discussion evidence that the work already exists, was fixed, can be closed, or is a duplicate is checked independently of this window.
- `ISSUEPILOT_DISCOVERY_CONCURRENCY` defaults to `6` and caps concurrent read-only GitHub checks between `1` and `8`.
- `ISSUEPILOT_DISCOVERY_SCAN_PAGES` defaults to `250` as a defensive ceiling. Discovery normally stops earlier when it fills the requested candidate count or exhausts GitHub's searchable result window for the current filters. Keep the value between `1` and `250`; lowering it creates a custom early-stop safety cap.
- `ISSUEPILOT_DISCOVERY_SORT` defaults to `recommended`. Supported values are `recommended`, `stars_desc`, `stars_asc`, `newest_issues`, and `recently_updated`.
- `ISSUEPILOT_WORKSPACE` defaults to `.oss-agent/workspaces` under the current working directory.
- Discovery history is stored locally at `.oss-agent/candidate-archive.json`. It keeps a deduplicated candidate snapshot plus the latest 100 scan records, capped at 2,000 candidates; it never stores GitHub tokens.

Change only settings needed for the user's request. Keep the workspace local and never commit `.env` or `.oss-agent/`.
# Repository-domain preferences

The local dashboard saves domain presets, custom keywords and exclusions into
`interests.json` beside the configured workspaces directory. CLI discovery uses
this same profile by default. Multiple domains and custom keywords are OR;
exclusions take precedence. Existing language, Stars, age and ownership checks
remain mandatory. Matching uses names, descriptions and Topics, not README or
Issue text, and adds no per-repository network request. It may miss repositories
with sparse metadata and is not an AI judgement of difficulty.

Overrides: `discover --domain ai`, repeat `--domain` for multiple presets;
`--keyword inference`, `--exclude-keyword trading`; `--all-domains` ignores the
saved profile for that invocation. Explicit overrides do not edit saved data.
