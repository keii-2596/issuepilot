# Continuous improvement

Use this reference only after a real IssuePilot run reveals a reproducible product flaw, such as a false recommendation, missed ownership signal, stale GitHub API assumption, unsafe race, or misleading status. Do not turn repository-specific maintainer preferences or a single subjective outcome into a universal rule.

## Improvement loop

1. Preserve evidence from the failed or misleading run and identify the smallest generalizable cause.
2. Prefer the current project root when it contains `src/issuepilot` and `codex-plugin/issuepilot`. Otherwise use the personal canonical source at `/Users/keii/Documents/github水pr` when it is available. Do not patch only the installed cache.
3. Make the narrowest change that fixes the demonstrated behavior. Keep bundled runtime copies synchronized with `src/issuepilot` when Python runtime files change.
4. Add or update a behavioral regression test that reproduces the failure without external writes. Avoid tests that only assert instruction wording.
5. Run the complete test suite against both `src` and the bundled plugin runtime. Validate the IssuePilot skill and plugin structure.
6. Use the `plugin-creator` cachebuster and personal-marketplace reinstall flow. Do not hand-edit marketplace configuration. Confirm the installed plugin is enabled and contains the new rule.
7. Tell the user what was learned, what changed, and how it was verified. Ask them to use a new Codex task when the refreshed skill must be loaded.

Limit the loop to one focused improvement for each concrete incident. If the cause is uncertain, tests cannot demonstrate the change, or the first correction still fails after one revision, stop and report the evidence instead of accumulating speculative rules.

## Invariants

- Never weaken explicit publication approval, the publish lock, token handling, Draft PR defaults, diff policy, hook protection, or external-write boundaries as an automatic improvement.
- Never publish, comment, assign, close, label, or otherwise mutate GitHub merely to test IssuePilot.
- Do not install dependencies globally. Use existing project or Codex runtimes, or a temporary isolated dependency directory.
- Ask before any improvement that broadens external side effects, requires new credentials, changes the user's marketplace choice, deletes material data, or alters behavior beyond IssuePilot's established scope.
- Preserve user work and unrelated local changes. Reinstallation may refresh the IssuePilot source and cache only.
