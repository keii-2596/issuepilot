# Obsolete workspace cleanup

Apply during daily runs and whenever a prepared candidate is reclassified as
obsolete, already fixed/implemented, duplicate, or closed with no remaining work.
The user has authorized this cleanup without asking again for each candidate.

1. Recheck the live Issue, discussion and relevant PR/commit or current code.
   Record the reason, evidence URLs and check time. Age alone, inactivity,
   heuristic wording, a closed unmerged PR, missing dependencies or a network
   error does not prove obsolescence. Assigned/claimed work and an open PR mean
   skip preparation, not automatically delete an existing human-review workspace.
2. Resolve the exact workspace from its IssuePilot record and verify its remote
   and Issue identity. It must be a direct child of the configured workspace root,
   not a symlink, broad directory or unrelated checkout. Check for active agent,
   build and review activity; if busy or ownership is unclear, defer cleanup.
3. Preserve a Chinese cleanup note with the evidence and disposition in the
   workspace review guide before removal. For ordinary standalone clones, use
   `RUNNER --json archive --issue URL`: this moves the entire workspace (including
   dirty/untracked files and the review guide) and manifest into the local archive.
   Verify the active path is gone and the archived workspace and manifest exist.
   Never use reset, clean, or recursive deletion to discard unreviewed edits.
4. If `.git` is a file identifying a real linked Git worktree, the clone archive
   helper is not appropriate. Inspect `git worktree list --porcelain` and use
   `git worktree move` to an explicit unique local archive destination where
   supported, preserving the Git registration and changes. If the move cannot
   preserve it safely (locked worktree, submodules, external ownership), report
   the blocker; do not force-remove it or prune other worktrees.
5. Keep a cleanup ledger outside active workspaces, beside the existing local
   archive: Issue URL, checked_at, terminal reason, evidence, original path,
   archive path and verified outcome. Preserve previous entries. Consult it when
   selecting archived candidates so the same obsolete issue is not prepared
   again just because its active manifest moved. Reconsider only on new evidence
   such as a reopened Issue or changed requirements.
6. Report what left the active directory and where it can be recovered. Archive
   retention is not permanent deletion or disk-space reclamation; do not claim
   space was freed. Permanent archive purging requires separate authorization.

Read-only review/status requests do not themselves trigger cleanup. Apply this
standing permission during preparation/maintenance runs, not incidental reads.
