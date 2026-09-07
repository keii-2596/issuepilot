# Preparing a maintainer-friendly contribution

Read this when preparing or revising a fix, or drafting its PR description.

- Read the repository's contribution instructions and the relevant module's recent commit/PR history. A shallow clone is not evidence that no history exists: use read-only GitHub history when needed. Look for design reasons, prior failed approaches, and actual test/release conventions; do not mechanically copy conventions from another project.
- Review every changed helper's callers. Broadening a predicate used for harmless discovery can also broaden destructive cleanup targets. Keep the latter's safety boundary intact; test both the original failure and important unaffected cases.
- For a process, hook, or cross-language integration bug, distinguish pure-function tests from wiring tests. Prefer a minimal in-repository regression that fails on the original implementation and passes on the fix. Record any real launch-chain or platform behavior not tested; test counts alone are not end-to-end evidence.
- Follow the repository's version and changelog rules. Regenerate derived version files with its generator. If consumers pin published packages, do not point them at a version that does not exist yet. A version bump is not authorization to tag or publish.
- Check newly introduced subprocess/network calls for bounded execution and useful failure diagnostics. Avoid turning a visible failure into a silent fallback that conceals the original problem.
- Draft a focused PR description: linked issue, root cause, scoped changes, exact validation and limitations. Be transparent about AI assistance without claiming human verification that did not happen. Use a non-closing reference while the fix remains partial. Keep local review guides and machine-specific paths out of the PR. Do not sign a CLA or post a review response for the user without their explicit authorization.
- A requested review is read-only. Make review fixes only when the user asks. If source changes after a prepared fingerprint was recorded, preserve that distinction and require normal revalidation before publishing; never silently replace the fingerprint to make the gate pass.
