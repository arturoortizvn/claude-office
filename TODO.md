# Pending items

Loose ends found while working, not yet scheduled. Each entry records the evidence
so the next session does not have to rediscover it.

---

## 1. `bump_version.py` does not relock, and nothing checks that it did

**Found**: 2026-08-18, while running `uv run` at the repo root.

The root `uv.lock` still pinned `claude-office 0.24.0` after `pyproject.toml` moved to
`0.24.1` — the whole v0.24.1 release shipped with a stale root lock. Any `uv run` at the
root silently rewrites the file and dirties the working tree, which is how it surfaced.
`backend/uv.lock` and `hooks/uv.lock` were both correct; only the root one drifted.

Two gaps let it through:

- `scripts/bump_version.py` rewrites the version strings but never runs `uv lock`.
- `make version-check` only compares the 6 declared locations. It does not inspect any
  lockfile, so it reported `OK: all 6 locations at 0.24.1` while the lock said `0.24.0`.

**Also**: `CLAUDE.md` documents "CI cross-check: `make version-check` (ARC-021)", but
`.github/workflows/ci.yml` never calls it. CI runs `make -C <component> checkall` for
backend, frontend, hooks and opencode-plugin, and nothing at the repo root — so neither
the version sync nor the root lock is guarded.

**Suggested fix**
- Have `bump_version.py` run `uv lock` for the root project after rewriting versions.
- Extend `--check` to fail when a lockfile disagrees with its `pyproject.toml`.
- Wire `make version-check` into `ci.yml` so the documented gate actually runs.

The lock itself was corrected in `9fa9abd`; this entry is about the mechanism that let it
drift, which is still in place.

---

## 2. `protect-git.sh` does not reliably block merges onto `main`

**Found**: 2026-08-18, merging two branches back to back.

Two `git merge --no-ff <branch>` commands were run against `main`, same shape, same target.
**The first was not blocked; the second was.** Pushes to `main` were blocked every time.

The hook lives at `~/.claude/hooks/protect-git.sh` — **local tooling, outside this repo**.
It is recorded here only so the finding is not lost; the fix does not touch this codebase.

Merging onto a protected branch is exactly the case the hook exists to catch, so the
inconsistency matters more than the push path that already works. Worth reproducing
deliberately and reading the matching logic before trusting it again.

---

## 3. Dependabot bump for Next 16.2.11 is unmerged

`origin/dependabot/npm_and_yarn/frontend/next-16.2.11` carries one commit not in `main`
(next 16.2.10 → 16.2.11, opened 2026-07-28). Left alone during the 2026-08-18 branch
cleanup because it is real unmerged work.

Two sibling branches inherited from upstream are also still on `origin` and were
deliberately left alone: `feat/i18n-multilanguage` (10 commits) and `feat/sidebar-resize`
(3 commits). Both still exist in `upstream`, so deleting the fork copies would lose
nothing — but that is a decision, not a cleanup.
